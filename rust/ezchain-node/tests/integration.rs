//! Integration tests: config validation, `check` (library and binary) and one `serve`
//! startup/shutdown smoke. Everything uses temp files and ephemeral 127.0.0.1 ports; no external
//! network, no HTTP client crate and no real key, ledger or money path.

use std::{
    fs,
    net::SocketAddr,
    path::PathBuf,
    process::Command as ProcessCommand,
    sync::atomic::{AtomicU64, Ordering},
};

use ezchain_node::{check, start, Config};
use serde_json::{json, Value};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
    sync::oneshot,
    time::{timeout, Duration},
};

static NEXT_ID: AtomicU64 = AtomicU64::new(0);

struct Fixture {
    config: PathBuf,
    validators: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        let stem = format!("ezchain-node-test-{}-{id}", std::process::id());
        let dir = std::env::temp_dir();
        Self {
            config: dir.join(format!("{stem}-config.json")),
            validators: dir.join(format!("{stem}-validators.json")),
        }
    }

    fn write_config(&self, value: &Value) {
        fs::write(&self.config, value.to_string()).expect("write config fixture");
    }

    fn write_validators(&self, value: &Value) {
        fs::write(&self.validators, value.to_string()).expect("write validator fixture");
    }

    fn valid_config(&self) -> Value {
        json!({
            "rpc_bind": "127.0.0.1:0",
            "rpc_max_payload_bytes": 65536,
            "validator_set_path": self.validators.to_str().expect("temp path is UTF-8"),
            "self_id": "node-a",
            "max_validators": 3,
            "p2p": {
                "max_envelope_bytes": 65536,
                "max_payload_bytes": 32768,
                "dedup_capacity": 1024,
                "queue_capacity": 256,
                "fanout": 1,
                "max_frame_bytes": 131072,
                "outbound_queue_capacity": 64,
                "accepted_queue_capacity": 16
            }
        })
    }

    fn write_valid_pair(&self) {
        self.write_validators(&json!({
            "version": "solo-1",
            "peers": ["node-a", "node-b", "node-c"]
        }));
        self.write_config(&self.valid_config());
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.config);
        let _ = fs::remove_file(&self.validators);
    }
}

fn validators(version: &str, peers: &[&str]) -> Value {
    json!({ "version": version, "peers": peers })
}

fn set_field(value: &mut Value, path: &[&str], replacement: Value) {
    let mut current = value;
    for key in &path[..path.len() - 1] {
        current = current.get_mut(key).expect("field exists in base config");
    }
    *current
        .get_mut(path[path.len() - 1])
        .expect("last field exists") = replacement;
}

#[test]
fn valid_config_loads_typed_fields_and_builds_both_transport_configs() {
    let fixture = Fixture::new();
    fixture.write_valid_pair();

    let config = Config::load(&fixture.config).expect("valid config loads");
    assert_eq!(config.rpc_bind.to_string(), "127.0.0.1:0");
    assert_eq!(config.rpc_max_payload_bytes, 65536);
    assert_eq!(config.self_id, "node-a");
    assert_eq!(config.max_validators, 3);
    assert_eq!(config.p2p.max_envelope_bytes, 65536);
    assert_eq!(config.p2p.max_frame_bytes, 131072);
    assert_eq!(config.p2p.fanout, 1);

    let set = config.load_validator_set().expect("valid set loads");
    assert_eq!(set.version(), "solo-1");
    assert_eq!(set.peers(), &["node-a", "node-b", "node-c"]);

    let rpc = config.rpc_config();
    assert_eq!(rpc.limits.max_payload_bytes, 65536);
    assert_eq!(rpc.granted_scopes, vec!["read".to_string()]);

    let p2p = config.p2p_config(set);
    assert_eq!(p2p.self_id, "node-a");
    assert_eq!(p2p.max_frame_bytes, 131072);
    assert_eq!(p2p.limits.max_payload_bytes, 32768);
}

#[test]
fn invalid_configs_are_typed_refusals() {
    let fixture = Fixture::new();
    fixture.write_validators(&validators("solo-1", &["node-a"]));
    let base = fixture.valid_config();

    let mut cases: Vec<(Value, &str)> = Vec::new();

    let mut value = base.clone();
    value.as_object_mut().unwrap().remove("rpc_bind");
    cases.push((value, "rpc_bind"));

    let mut value = base.clone();
    value
        .as_object_mut()
        .unwrap()
        .insert("live_mode".to_string(), json!(true));
    cases.push((value, "live_mode"));

    let mut value = base.clone();
    set_field(&mut value, &["rpc_bind"], json!("not-a-socket"));
    cases.push((value, "rpc_bind"));

    let mut value = base.clone();
    set_field(&mut value, &["rpc_max_payload_bytes"], json!(0));
    cases.push((value, "rpc_max_payload_bytes"));

    let mut value = base.clone();
    set_field(
        &mut value,
        &["rpc_max_payload_bytes"],
        json!(4_294_967_296u64),
    );
    cases.push((value, "rpc_max_payload_bytes"));

    let mut value = base.clone();
    set_field(&mut value, &["self_id"], json!(""));
    cases.push((value, "self_id"));

    let mut value = base.clone();
    set_field(&mut value, &["validator_set_path"], json!(""));
    cases.push((value, "validator_set_path"));

    let mut value = base.clone();
    set_field(&mut value, &["max_validators"], json!(0));
    cases.push((value, "max_validators"));

    let mut value = base.clone();
    value
        .get_mut("p2p")
        .unwrap()
        .as_object_mut()
        .unwrap()
        .remove("fanout");
    cases.push((value, "fanout"));

    let mut value = base.clone();
    set_field(
        &mut value,
        &["p2p", "max_frame_bytes"],
        json!(4_294_967_296u64),
    );
    cases.push((value, "max_frame_bytes"));

    let mut value = base.clone();
    set_field(&mut value, &["p2p", "max_envelope_bytes"], json!(200_000));
    cases.push((value, "max_envelope_bytes"));

    let mut value = base.clone();
    set_field(&mut value, &["p2p", "max_payload_bytes"], json!(70_000));
    cases.push((value, "max_payload_bytes"));

    for (value, expected_field) in cases {
        fixture.write_config(&value);
        let error = Config::load(&fixture.config).expect_err("invalid config must be refused");
        let message = error.to_string();
        assert!(
            message.contains(expected_field),
            "expected '{expected_field}' in: {message}"
        );
    }

    fs::write(&fixture.config, "{").expect("write malformed JSON");
    assert!(
        Config::load(&fixture.config).is_err(),
        "malformed JSON is refused"
    );

    // The base fixture itself is valid, so the refusals above are about the mutations.
    fixture.write_config(&base);
    assert!(Config::load(&fixture.config).is_ok());
}

#[test]
fn validator_set_failures_are_typed_and_self_membership_is_checked() {
    let fixture = Fixture::new();
    fixture.write_config(&fixture.valid_config());
    let config = Config::load(&fixture.config).expect("config is valid on its own");

    let mut cases: Vec<(Value, &str)> = vec![
        (validators("", &["node-a"]), "'version': must not be empty"),
        (validators("s-1", &[]), "peers must not be empty"),
        (validators("s-1", &[""]), "peers[]"),
        (validators("s-1", &["node-a", "node-a"]), "DuplicatePeer"),
        (
            validators("s-1", &["node-b", "node-c", "node-d", "node-a"]),
            "TooManyPeers",
        ),
        (validators("s-1", &["node-b"]), "self_id 'node-a'"),
    ];
    cases.push((
        json!({ "version": "s-1", "peers": ["node-a"], "extra": 1 }),
        "extra",
    ));
    cases.push((json!({ "version": "s-1" }), "peers"));

    for (value, expected) in cases {
        fixture.write_validators(&value);
        let error = config
            .load_validator_set()
            .expect_err("invalid validator set must be refused");
        let message = error.to_string();
        assert!(
            message.contains(expected),
            "expected '{expected}' in: {message}"
        );
    }

    let _ = fs::remove_file(&fixture.validators);
    assert!(
        config.load_validator_set().is_err(),
        "missing validator set file is refused"
    );
}

#[test]
fn check_accepts_a_temp_config_and_the_binary_reports_the_same_result() {
    let fixture = Fixture::new();
    fixture.write_validators(&validators("solo-1", &["node-a"]));
    fixture.write_config(&fixture.valid_config());

    let lines = check(&fixture.config).expect("check accepts valid config");
    assert_eq!(lines[0], "ezchain-node/1 check ok");
    assert!(lines.iter().any(|line| line.contains("validator_count=1")));
    assert!(lines.iter().any(|line| line.contains("live=owner-blocked")));

    let binary = env!("CARGO_BIN_EXE_ezchain-node");
    let config_arg = fixture.config.to_str().expect("temp path is UTF-8");
    let ok = ProcessCommand::new(binary)
        .args(["check", "--config", config_arg])
        .output()
        .expect("run check binary");
    assert!(ok.status.success(), "check exit 0: {:?}", ok.status);
    let stdout = String::from_utf8(ok.stdout).expect("UTF-8 stdout");
    assert!(stdout.contains("check ok"), "{stdout}");

    let bad_config = fixture.config.with_extension("bad.json");
    fs::write(&bad_config, "{").expect("write bad config");
    let bad = ProcessCommand::new(binary)
        .args(["check", "--config", bad_config.to_str().unwrap()])
        .output()
        .expect("run check binary on invalid config");
    assert_eq!(bad.status.code(), Some(2), "refusal exits 2");
    let stderr = String::from_utf8(bad.stderr).expect("UTF-8 stderr");
    assert!(stderr.contains("invalid JSON"), "{stderr}");
    let _ = fs::remove_file(&bad_config);
}

/// One raw HTTP/1.1 request, no HTTP client crate and no external network.
async fn healthz(addr: SocketAddr) -> String {
    let mut stream = timeout(Duration::from_secs(3), TcpStream::connect(addr))
        .await
        .expect("connect within three seconds")
        .expect("connect to RPC listener");
    stream
        .write_all(b"GET /v1/healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await
        .expect("write health request");
    let mut raw = Vec::new();
    timeout(Duration::from_secs(3), stream.read_to_end(&mut raw))
        .await
        .expect("read health response within three seconds")
        .expect("read health response");
    String::from_utf8_lossy(&raw).to_string()
}

#[tokio::test]
async fn serve_starts_both_transports_and_shuts_down_on_one_signal() {
    let fixture = Fixture::new();
    fixture.write_validators(&validators("solo-1", &["node-a"]));
    fixture.write_config(&fixture.valid_config());
    let config = Config::load(&fixture.config).expect("valid config loads");

    let (shutdown, receiver) = oneshot::channel::<()>();
    let running = start(config, receiver)
        .await
        .expect("both transports start");
    let summary = running.summary();

    assert!(summary.to_string().contains("ezchain-node/1 startup ok"));
    assert!(summary.to_string().contains("live=owner-blocked"));
    assert_ne!(
        summary.rpc_bind.port(),
        0,
        "RPC binds a real ephemeral port"
    );
    assert_ne!(
        summary.p2p_bind.port(),
        0,
        "P2P binds a real ephemeral port"
    );
    assert_eq!(summary.set_version, "solo-1");
    assert_eq!(summary.validator_count, 1);
    assert_eq!(summary.rpc_max_payload_bytes, 65536);

    let response = healthz(summary.rpc_bind).await;
    assert!(response.contains("200 OK"), "{response}");
    assert!(
        response.contains("ezchain.rpc-node/1 healthz ok"),
        "{response}"
    );

    // The P2P accept loop is live on its ephemeral localhost port.
    let _peer = timeout(Duration::from_secs(3), TcpStream::connect(summary.p2p_bind))
        .await
        .expect("connect to P2P within three seconds")
        .expect("connect to P2P listener");

    shutdown.send(()).expect("receiver still alive");
    timeout(Duration::from_secs(5), running.shutdown())
        .await
        .expect("clean shutdown within five seconds")
        .expect("both transports stopped cleanly");
}
