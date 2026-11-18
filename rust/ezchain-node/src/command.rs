//! Command execution: `check` validates without I/O beyond reading config files; `serve`
//! composes the transports and prints the typed startup summary.

use std::{fmt, path::Path};

use tokio::sync::oneshot;

use crate::{
    args::{parse, Command, ParseError},
    config::{Config, ConfigError},
    node::{start, ServeError},
};

/// A typed command error. A bad command line, bad config and failed startup are distinct.
#[derive(Debug)]
pub enum CliError {
    Parse(ParseError),
    Config(ConfigError),
    Serve(ServeError),
}

impl fmt::Display for CliError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CliError::Parse(error) => write!(f, "{error}"),
            CliError::Config(error) => write!(f, "{error}"),
            CliError::Serve(error) => write!(f, "{error}"),
        }
    }
}

impl std::error::Error for CliError {}

impl From<ParseError> for CliError {
    fn from(error: ParseError) -> Self {
        Self::Parse(error)
    }
}

impl From<ConfigError> for CliError {
    fn from(error: ConfigError) -> Self {
        Self::Config(error)
    }
}

impl From<ServeError> for CliError {
    fn from(error: ServeError) -> Self {
        Self::Serve(error)
    }
}

/// Validate a config file and the validator set it names, without binding any socket or
/// starting any transport. The returned lines are the exact `check` output.
pub fn check(path: &Path) -> Result<Vec<String>, ConfigError> {
    let config = Config::load(path)?;
    let validators = config.load_validator_set()?;
    // Build both transport configs to prove they are accepted; this opens no socket.
    let _ = config.rpc_config();
    let _ = config.p2p_config(validators.clone());

    Ok(vec![
        "ezchain-node/1 check ok".to_string(),
        format!("config={}", path.display()),
        format!(
            "validator_set_version={} validator_count={} self_id={}",
            validators.version(),
            validators.len(),
            config.self_id
        ),
        format!(
            "rpc_bind={} rpc_max_payload_bytes={}",
            config.rpc_bind, config.rpc_max_payload_bytes
        ),
        "mode=kernel-only live=owner-blocked".to_string(),
    ])
}

/// Parse and execute a command line, returning the exact lines to print on stdout.
pub async fn run(args: &[String]) -> Result<Vec<String>, CliError> {
    match parse(args)? {
        Command::Check { config } => check(&config).map_err(CliError::Config),
        Command::Help => Ok(help()),
        Command::Serve { config } => {
            let config = Config::load(&config)?;
            let (shutdown, receiver) = oneshot::channel::<()>();
            let running = start(config, receiver).await?;
            let lines = vec![running.summary().to_string()];

            // The single shutdown signal is Enter/EOF on stdin. A blocking reader is used so no
            // signal feature (and no extra crate) is needed; it is aborted after clean shutdown.
            let stdin_task = tokio::task::spawn_blocking(move || {
                let mut line = String::new();
                let _ = std::io::stdin().read_line(&mut line);
                let _ = shutdown.send(());
            });
            let result = running.shutdown().await;
            stdin_task.abort();
            result?;
            Ok(lines)
        }
    }
}

fn help() -> Vec<String> {
    vec![
        "ezchain-node/1 — kernel-only composition of the Rust node transports".to_string(),
        "usage: ezchain-node check --config <file>".to_string(),
        "       ezchain-node serve --config <file>   (Enter/EOF on stdin to stop)".to_string(),
        "live money and vault keys stay owner-blocked; no ledger, keys or venue calls".to_string(),
    ]
}
