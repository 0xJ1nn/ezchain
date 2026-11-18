//! Concurrent composition of the two node transports under one shutdown signal.
//!
//! `start` binds the configurable RPC port and the P2P transport's ephemeral localhost port,
//! then returns a [`RunningNode`] exposing a typed startup summary. A single oneshot receiver
//! drives both: it is handed to the RPC server's graceful-shutdown future, and when that server
//! drains, [`RunningNode::shutdown`] closes the P2P node too. No socket is opened in `check`.

use std::{fmt, io, net::SocketAddr};

use ezchain_p2p_transport::Node;
use tokio::{net::TcpListener, sync::oneshot, task::JoinHandle};

use crate::config::{Config, ConfigError};

/// A typed startup/shutdown failure. No path panics or silently ignores a bind error.
#[derive(Debug)]
pub enum ServeError {
    Config(ConfigError),
    Bind(io::Error),
    Transport(String),
    Task(String),
}

impl fmt::Display for ServeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ServeError::Config(error) => write!(f, "{error}"),
            ServeError::Bind(error) => write!(f, "cannot bind RPC listener: {error}"),
            ServeError::Transport(error) => write!(f, "P2P transport error: {error}"),
            ServeError::Task(error) => write!(f, "RPC task error: {error}"),
        }
    }
}

impl std::error::Error for ServeError {}

/// The typed startup summary printed by `ezchain-node serve`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartupSummary {
    pub rpc_bind: SocketAddr,
    pub p2p_bind: SocketAddr,
    pub self_id: String,
    pub set_version: String,
    pub validator_count: usize,
    pub rpc_max_payload_bytes: usize,
    pub p2p_max_frame_bytes: usize,
}

impl fmt::Display for StartupSummary {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "ezchain-node/1 startup ok")?;
        writeln!(f, "rpc_bind={}", self.rpc_bind)?;
        writeln!(f, "p2p_bind={}", self.p2p_bind)?;
        writeln!(
            f,
            "validator_set_version={} validator_count={} self_id={}",
            self.set_version, self.validator_count, self.self_id
        )?;
        writeln!(
            f,
            "rpc_max_payload_bytes={} p2p_max_frame_bytes={}",
            self.rpc_max_payload_bytes, self.p2p_max_frame_bytes
        )?;
        write!(f, "mode=kernel-only live=owner-blocked")
    }
}

/// Both transports live. Call [`RunningNode::shutdown`] for a clean stop; dropping it does not
/// guarantee task cleanup (the P2P transport has the same rule).
pub struct RunningNode {
    summary: StartupSummary,
    node: Node,
    rpc: JoinHandle<io::Result<()>>,
}

impl RunningNode {
    pub fn summary(&self) -> &StartupSummary {
        &self.summary
    }

    /// Resolve the single shutdown signal, drain RPC in-flight requests, then close the P2P node.
    pub async fn shutdown(self) -> Result<(), ServeError> {
        let rpc_result = self
            .rpc
            .await
            .map_err(|err| ServeError::Task(err.to_string()))?;
        self.node
            .shutdown()
            .await
            .map_err(|err| ServeError::Transport(err.to_string()))?;
        rpc_result.map_err(ServeError::Bind)?;
        Ok(())
    }
}

/// Bind both transports and return immediately; the shutdown receiver must resolve to stop them.
pub async fn start(
    config: Config,
    shutdown: oneshot::Receiver<()>,
) -> Result<RunningNode, ServeError> {
    let validators = config.load_validator_set().map_err(ServeError::Config)?;
    let rpc_config = config.rpc_config();
    let p2p_config = config.p2p_config(validators.clone());

    let listener = TcpListener::bind(config.rpc_bind)
        .await
        .map_err(ServeError::Bind)?;
    let rpc_bind = listener.local_addr().map_err(ServeError::Bind)?;
    let node = Node::bind(p2p_config)
        .await
        .map_err(|err| ServeError::Transport(err.to_string()))?;

    let summary = StartupSummary {
        rpc_bind,
        p2p_bind: node.local_addr(),
        self_id: config.self_id,
        set_version: validators.version().to_string(),
        validator_count: validators.len(),
        rpc_max_payload_bytes: config.rpc_max_payload_bytes,
        p2p_max_frame_bytes: config.p2p.max_frame_bytes,
    };

    // The one signal is the RPC server's graceful-shutdown future. The P2P node is stopped in
    // `RunningNode::shutdown` once that future completes, so there is no second stop path.
    let rpc = tokio::spawn(ezchain_rpc_server::serve(
        listener,
        rpc_config,
        async move {
            let _ = shutdown.await;
        },
    ));

    Ok(RunningNode { summary, node, rpc })
}
