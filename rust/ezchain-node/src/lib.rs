//! # ezchain-node
//!
//! The composition binary for the ezCHAIN Rust node: one typed JSON configuration, a `check`
//! command that validates it without starting anything, and a `serve` command that starts the
//! node-facing RPC transport and the P2P transport concurrently under one shutdown signal.
//!
//! This crate **composes** existing kernels; it does not reimplement them. It contains no keys,
//! signatures, money arithmetic, ledger writes, venue calls or live-mode path. Real auth
//! resolution and real dispatch handlers remain future work; the RPC transport still serves the
//! pure typed ack handlers only (ADR-0050/0052, CONTRACT I1, I4).
//!
//! Every function that touches a socket is async and takes an explicit shutdown signal, so the
//! startup/shutdown contract is testable on ephemeral localhost ports without external network.

#![forbid(unsafe_code)]

pub mod args;
pub mod command;
pub mod config;
pub mod node;

pub use args::{parse, Command, ParseError};
pub use command::{check, run, CliError};
pub use config::{Config, ConfigError, P2pConfig};
pub use node::{start, RunningNode, ServeError, StartupSummary};
