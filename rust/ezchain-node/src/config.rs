//! Typed node configuration, loaded from one small JSON file.
//!
//! The config is the single home for every size and bound the two transports need (CONTRACT I7):
//! no number appearing here is duplicated as a literal in a transport kernel. ADR-0051 is still
//! Proposed and decides no validator-set size, fanout or quorum, so the operator supplies them
//! here and nothing is silently defaulted. `check` parses and validates everything — including
//! the operator-provisioned validator set file — without binding a socket.
//!
//! JSON is parsed with `serde_json`; the `Deserialize` impls are hand-written so that an unknown
//! field, a missing field or a wrong type is a typed refusal rather than a guess (CONTRACT I3).

use std::{
    fmt, fs,
    net::SocketAddr,
    path::{Path, PathBuf},
};

use ezchain_p2p::{
    envelope::Limits as EnvelopeLimits,
    peer::{PeerId, ValidatorSet},
};
use ezchain_p2p_transport::NodeConfig;
use ezchain_rpc::framing::Limits as FrameLimits;
use ezchain_rpc_server::Config as RpcConfig;
use serde::{
    de::{Deserializer, Error as DeError},
    Deserialize,
};
use serde_json::{Map, Value};

/// The P2P frame body is the canonical envelope plus a 64-bit digest (transport wire contract).
const ENVELOPE_DIGEST_BYTES: usize = 8;

const CONFIG_FIELDS: &[&str] = &[
    "rpc_bind",
    "rpc_max_payload_bytes",
    "validator_set_path",
    "self_id",
    "max_validators",
    "p2p",
];
const P2P_FIELDS: &[&str] = &[
    "max_envelope_bytes",
    "max_payload_bytes",
    "dedup_capacity",
    "queue_capacity",
    "fanout",
    "max_frame_bytes",
    "outbound_queue_capacity",
    "accepted_queue_capacity",
];
const VALIDATOR_SET_FIELDS: &[&str] = &["version", "peers"];

/// Every caller-supplied knob of the P2P transport and its pure kernel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct P2pConfig {
    pub max_envelope_bytes: usize,
    pub max_payload_bytes: usize,
    pub dedup_capacity: usize,
    pub queue_capacity: usize,
    pub fanout: usize,
    pub max_frame_bytes: usize,
    pub outbound_queue_capacity: usize,
    pub accepted_queue_capacity: usize,
}

/// A fully typed, validated node configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub rpc_bind: SocketAddr,
    pub rpc_max_payload_bytes: usize,
    pub validator_set_path: PathBuf,
    pub self_id: String,
    pub max_validators: usize,
    pub p2p: P2pConfig,
}

/// A typed configuration failure. No variant panics or silently falls back.
#[derive(Debug)]
pub enum ConfigError {
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
    Json {
        path: PathBuf,
        source: serde_json::Error,
    },
    Invalid {
        field: String,
        reason: String,
    },
    ValidatorSet {
        path: PathBuf,
        reason: String,
    },
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::Io { path, source } => {
                write!(f, "cannot read '{}': {source}", path.display())
            }
            ConfigError::Json { path, source } => {
                write!(f, "invalid JSON in '{}': {source}", path.display())
            }
            ConfigError::Invalid { field, reason } => {
                write!(f, "invalid config field '{field}': {reason}")
            }
            ConfigError::ValidatorSet { path, reason } => {
                write!(f, "invalid validator set '{}': {reason}", path.display())
            }
        }
    }
}

impl std::error::Error for ConfigError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            ConfigError::Io { source, .. } => Some(source),
            ConfigError::Json { source, .. } => Some(source),
            _ => None,
        }
    }
}

impl<'de> Deserialize<'de> for Config {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = Value::deserialize(deserializer)?;
        Self::from_value(&value).map_err(<D::Error as DeError>::custom)
    }
}

impl Config {
    /// Read and validate a config file. Nothing is bound or started.
    pub fn load(path: &Path) -> Result<Self, ConfigError> {
        let text = fs::read_to_string(path).map_err(|source| ConfigError::Io {
            path: path.to_path_buf(),
            source,
        })?;
        serde_json::from_str::<Config>(&text).map_err(|source| ConfigError::Json {
            path: path.to_path_buf(),
            source,
        })
    }

    /// Build the operator-provisioned validator set, checking this node's membership.
    pub fn load_validator_set(&self) -> Result<ValidatorSet, ConfigError> {
        let path = &self.validator_set_path;
        let text = fs::read_to_string(path).map_err(|source| ConfigError::Io {
            path: path.clone(),
            source,
        })?;
        let raw: ValidatorSetFile =
            serde_json::from_str(&text).map_err(|source| ConfigError::Json {
                path: path.clone(),
                source,
            })?;
        if raw.peers.is_empty() {
            return Err(ConfigError::ValidatorSet {
                path: path.clone(),
                reason: "peers must not be empty".to_string(),
            });
        }
        let set =
            ValidatorSet::new(raw.version, raw.peers, self.max_validators).map_err(|err| {
                ConfigError::ValidatorSet {
                    path: path.clone(),
                    reason: format!("{err:?}"),
                }
            })?;
        if !set.contains(&self.self_id) {
            return Err(ConfigError::ValidatorSet {
                path: path.clone(),
                reason: format!("self_id '{}' is not a member of the set", self.self_id),
            });
        }
        Ok(set)
    }

    /// The RPC transport config. The read-only placeholder grant comes from the transport;
    /// resolving a real principal is future work (ADR-0052).
    pub fn rpc_config(&self) -> RpcConfig {
        RpcConfig::new(FrameLimits {
            max_payload_bytes: self.rpc_max_payload_bytes,
        })
    }

    /// The P2P transport config. All policy numbers come from this file (ADR-0051, CONTRACT I7).
    pub fn p2p_config(&self, validators: ValidatorSet) -> NodeConfig {
        NodeConfig {
            self_id: self.self_id.clone(),
            validators,
            limits: EnvelopeLimits {
                max_envelope_bytes: self.p2p.max_envelope_bytes,
                max_payload_bytes: self.p2p.max_payload_bytes,
            },
            dedup_capacity: self.p2p.dedup_capacity,
            queue_capacity: self.p2p.queue_capacity,
            fanout: self.p2p.fanout,
            max_frame_bytes: self.p2p.max_frame_bytes,
            outbound_queue_capacity: self.p2p.outbound_queue_capacity,
            accepted_queue_capacity: self.p2p.accepted_queue_capacity,
        }
    }

    fn from_value(value: &Value) -> Result<Self, ConfigError> {
        let object = as_object(value, "$")?;
        reject_unknown(object, CONFIG_FIELDS)?;

        let rpc_bind = parse_bind(&string_field(object, "rpc_bind")?)?;
        let rpc_max_payload_bytes = positive_usize(object, "rpc_max_payload_bytes")?;
        if rpc_max_payload_bytes > u32::MAX as usize {
            return Err(ConfigError::Invalid {
                field: "rpc_max_payload_bytes".to_string(),
                reason: "exceeds the 4-byte frame length prefix ceiling".to_string(),
            });
        }
        let validator_set_path = PathBuf::from(non_empty_string(object, "validator_set_path")?);
        let self_id = non_empty_string(object, "self_id")?;
        let max_validators = positive_usize(object, "max_validators")?;

        let p2p_value = object.get("p2p").ok_or_else(|| missing_field("p2p"))?;
        let p2p = P2pConfig::from_value(p2p_value)?;

        Ok(Self {
            rpc_bind,
            rpc_max_payload_bytes,
            validator_set_path,
            self_id,
            max_validators,
            p2p,
        })
    }
}

impl P2pConfig {
    fn from_value(value: &Value) -> Result<Self, ConfigError> {
        let object = as_object(value, "p2p")?;
        reject_unknown(object, P2P_FIELDS)?;
        let config = Self {
            max_envelope_bytes: positive_usize(object, "max_envelope_bytes")?,
            max_payload_bytes: positive_usize(object, "max_payload_bytes")?,
            dedup_capacity: positive_usize(object, "dedup_capacity")?,
            queue_capacity: positive_usize(object, "queue_capacity")?,
            fanout: positive_usize(object, "fanout")?,
            max_frame_bytes: positive_usize(object, "max_frame_bytes")?,
            outbound_queue_capacity: positive_usize(object, "outbound_queue_capacity")?,
            accepted_queue_capacity: positive_usize(object, "accepted_queue_capacity")?,
        };
        if config.max_frame_bytes > u32::MAX as usize {
            return Err(ConfigError::Invalid {
                field: "p2p.max_frame_bytes".to_string(),
                reason: "exceeds the 4-byte frame length prefix ceiling".to_string(),
            });
        }
        if config.max_payload_bytes > config.max_envelope_bytes {
            return Err(ConfigError::Invalid {
                field: "p2p.max_payload_bytes".to_string(),
                reason: "must not exceed p2p.max_envelope_bytes".to_string(),
            });
        }
        if config
            .max_envelope_bytes
            .checked_add(ENVELOPE_DIGEST_BYTES)
            .is_none_or(|body| body > config.max_frame_bytes)
        {
            return Err(ConfigError::Invalid {
                field: "p2p.max_envelope_bytes".to_string(),
                reason: "envelope plus digest must fit inside p2p.max_frame_bytes".to_string(),
            });
        }
        Ok(config)
    }
}

/// The operator-provisioned validator file: an ordered list of opaque ids and a set version.
#[derive(Debug)]
struct ValidatorSetFile {
    version: String,
    peers: Vec<PeerId>,
}

impl<'de> Deserialize<'de> for ValidatorSetFile {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = Value::deserialize(deserializer)?;
        let object = as_object(&value, "validator set").map_err(<D::Error as DeError>::custom)?;
        reject_unknown(object, VALIDATOR_SET_FIELDS).map_err(<D::Error as DeError>::custom)?;
        let version = non_empty_string(object, "version").map_err(<D::Error as DeError>::custom)?;
        let peers_value = object
            .get("peers")
            .ok_or_else(|| <D::Error as DeError>::custom(missing_field("peers")))?;
        let peers = peers_value
            .as_array()
            .ok_or_else(|| {
                <D::Error as DeError>::custom(wrong_type("peers", "array of non-empty strings"))
            })?
            .iter()
            .map(|item| {
                item.as_str()
                    .filter(|text| !text.is_empty())
                    .map(str::to_string)
                    .ok_or_else(|| {
                        <D::Error as DeError>::custom(ConfigError::Invalid {
                            field: "peers[]".to_string(),
                            reason: "each peer id must be a non-empty string".to_string(),
                        })
                    })
            })
            .collect::<Result<Vec<PeerId>, D::Error>>()?;
        Ok(Self { version, peers })
    }
}

fn as_object<'a>(value: &'a Value, field: &str) -> Result<&'a Map<String, Value>, ConfigError> {
    value
        .as_object()
        .ok_or_else(|| wrong_type(field, "JSON object"))
}

fn reject_unknown(object: &Map<String, Value>, allowed: &[&str]) -> Result<(), ConfigError> {
    for key in object.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(ConfigError::Invalid {
                field: key.clone(),
                reason: "unknown field".to_string(),
            });
        }
    }
    Ok(())
}

fn string_field(object: &Map<String, Value>, field: &str) -> Result<String, ConfigError> {
    let value = object.get(field).ok_or_else(|| missing_field(field))?;
    value
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| wrong_type(field, "string"))
}

fn non_empty_string(object: &Map<String, Value>, field: &str) -> Result<String, ConfigError> {
    let value = string_field(object, field)?;
    if value.is_empty() {
        return Err(ConfigError::Invalid {
            field: field.to_string(),
            reason: "must not be empty".to_string(),
        });
    }
    Ok(value)
}

fn positive_usize(object: &Map<String, Value>, field: &str) -> Result<usize, ConfigError> {
    let value = object.get(field).ok_or_else(|| missing_field(field))?;
    let number = value
        .as_u64()
        .ok_or_else(|| wrong_type(field, "a positive integer"))?;
    let size = usize::try_from(number).map_err(|_| ConfigError::Invalid {
        field: field.to_string(),
        reason: "does not fit in usize".to_string(),
    })?;
    if size == 0 {
        return Err(ConfigError::Invalid {
            field: field.to_string(),
            reason: "must be greater than zero".to_string(),
        });
    }
    Ok(size)
}

fn parse_bind(text: &str) -> Result<SocketAddr, ConfigError> {
    text.parse::<SocketAddr>()
        .map_err(|err| ConfigError::Invalid {
            field: "rpc_bind".to_string(),
            reason: format!("not a socket address: {err}"),
        })
}

fn missing_field(field: &str) -> ConfigError {
    ConfigError::Invalid {
        field: field.to_string(),
        reason: "missing required field".to_string(),
    }
}

fn wrong_type(field: &str, expected: &str) -> ConfigError {
    ConfigError::Invalid {
        field: field.to_string(),
        reason: format!("expected {expected}"),
    }
}
