//! Typed connection, protocol and configuration errors for the transport.

use std::{error::Error as StdError, fmt, io};

use ezchain_p2p::kernel::KernelError;

use crate::codec::CodecError;

/// A typed failure from the transport. Variants name the operation that failed so a caller can
/// distinguish a bad frame from a dial failure without parsing a string.
#[derive(Debug)]
pub enum TransportError {
    /// A caller-supplied bound was zero or exceeded the 4-byte prefix ceiling.
    InvalidConfig(&'static str),
    /// The ephemeral localhost listener could not be bound.
    Bind(io::Error),
    /// An inbound connection could not be accepted (or the accept backlog closed).
    Accept(io::Error),
    /// An outbound connection could not be dialled.
    Connect(io::Error),
    /// A frame was malformed or exceeded its bound.
    Codec(CodecError),
    /// The pure kernel rejected the node configuration.
    Kernel(KernelError),
    /// The operator tried to bind a label that is already connected.
    PeerAlreadyConnected(String),
    /// A send was addressed to a peer with no live connection.
    PeerNotConnected(String),
    /// The peer's outbound queue closed because its connection ended.
    PeerDisconnected(String),
    /// The node shut down while an operation was in progress.
    Shutdown,
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidConfig(field) => write!(f, "invalid transport config: {field}"),
            Self::Bind(e) => write!(f, "bind failed: {e}"),
            Self::Accept(e) => write!(f, "accept failed: {e}"),
            Self::Connect(e) => write!(f, "connect failed: {e}"),
            Self::Codec(e) => write!(f, "protocol error: {e}"),
            Self::Kernel(e) => write!(f, "kernel config rejected: {e:?}"),
            Self::PeerAlreadyConnected(peer) => {
                write!(f, "peer already connected: {peer}")
            }
            Self::PeerNotConnected(peer) => write!(f, "peer not connected: {peer}"),
            Self::PeerDisconnected(peer) => write!(f, "peer disconnected: {peer}"),
            Self::Shutdown => write!(f, "node is shutting down"),
        }
    }
}

impl StdError for TransportError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Self::Bind(e) | Self::Accept(e) | Self::Connect(e) => Some(e),
            Self::Codec(e) => Some(e),
            _ => None,
        }
    }
}

impl From<CodecError> for TransportError {
    fn from(error: CodecError) -> Self {
        Self::Codec(error)
    }
}
