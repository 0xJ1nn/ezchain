//! The minimal `tokio` TCP transport node.
//!
//! A [`Node`] binds an ephemeral localhost listener, accepts or dials operator-labelled peer
//! connections, reads bounded length-prefixed envelopes and hands them to the pure
//! [`GossipKernel`]. Accepted fanout deliveries are written verbatim to the connections whose
//! labels the kernel selected. There is no discovery, handshake, authentication, key, money or
//! ledger path here; see the crate documentation and README.

use std::{
    collections::HashMap,
    net::SocketAddr,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
};

use ezchain_p2p::{
    envelope::{Envelope, Limits},
    kernel::{GossipKernel, KernelConfig},
    peer::{PeerId, ValidatorSet},
    trace::DeliveryTrace,
};
use tokio::{
    io::{split, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{mpsc, oneshot, watch, Mutex as AsyncMutex},
    task::JoinHandle,
};

use crate::{codec, error::TransportError};

const LOCALHOST: &str = "127.0.0.1";

/// Everything the transport and its kernel need, supplied by the caller. ADR-0051 is Proposed:
/// the validator set, set version, fanout and all capacities are parameters, never literals
/// (CONTRACT I7). No quorum number appears because consensus is out of scope here.
#[derive(Debug, Clone)]
pub struct NodeConfig {
    /// This node's opaque id; must be a member of `validators` (checked by the kernel).
    pub self_id: PeerId,
    /// The operator-provisioned validator set and its version label.
    pub validators: ValidatorSet,
    /// Envelope size limits passed to the kernel validator.
    pub limits: Limits,
    /// Bounded deduplicator capacity passed to the kernel.
    pub dedup_capacity: usize,
    /// Bounded inbound queue capacity passed to the kernel.
    pub queue_capacity: usize,
    /// Fanout count passed to the kernel.
    pub fanout: usize,
    /// Maximum framed body size on the wire (bounded frame size, no unbounded allocation).
    pub max_frame_bytes: usize,
    /// Bounded per-connection outbound frame queue.
    pub outbound_queue_capacity: usize,
    /// Bounded backlog of accepted-but-not-yet-labelled inbound connections.
    pub accepted_queue_capacity: usize,
}

impl NodeConfig {
    fn validate(&self) -> Result<(), TransportError> {
        if self.max_frame_bytes == 0 {
            return Err(TransportError::InvalidConfig(
                "max_frame_bytes must be greater than zero",
            ));
        }
        // A frame length is carried in u32, so any larger bound could be truncated on encode.
        if self.max_frame_bytes > u32::MAX as usize {
            return Err(TransportError::InvalidConfig(
                "max_frame_bytes exceeds the 4-byte frame prefix ceiling",
            ));
        }
        if self.outbound_queue_capacity == 0 {
            return Err(TransportError::InvalidConfig(
                "outbound_queue_capacity must be greater than zero",
            ));
        }
        if self.accepted_queue_capacity == 0 {
            return Err(TransportError::InvalidConfig(
                "accepted_queue_capacity must be greater than zero",
            ));
        }
        Ok(())
    }

    fn kernel_config(&self) -> KernelConfig {
        KernelConfig {
            self_id: self.self_id.clone(),
            validators: self.validators.clone(),
            limits: self.limits,
            dedup_capacity: self.dedup_capacity,
            queue_capacity: self.queue_capacity,
            fanout: self.fanout,
        }
    }
}

/// Outbound frame senders keyed by operator-supplied peer label. Each registered sender carries
/// a unique connection token so a closing connection cannot remove a newer connection that
/// reused the same label.
/// A connection's registered outbound sender together with its unique connection token.
type RegisteredSender = (u64, mpsc::Sender<Vec<u8>>);

#[derive(Default)]
struct PeerRegistry {
    senders: Mutex<HashMap<PeerId, RegisteredSender>>,
    next_token: AtomicU64,
}

impl PeerRegistry {
    fn register(
        &self,
        label: PeerId,
        sender: mpsc::Sender<Vec<u8>>,
    ) -> Result<u64, TransportError> {
        let mut senders = self.senders.lock().unwrap();
        if senders.contains_key(&label) {
            return Err(TransportError::PeerAlreadyConnected(label));
        }
        let token = self.next_token.fetch_add(1, Ordering::Relaxed);
        senders.insert(label, (token, sender));
        Ok(token)
    }

    fn get(&self, label: &PeerId) -> Option<mpsc::Sender<Vec<u8>>> {
        self.senders
            .lock()
            .unwrap()
            .get(label)
            .map(|(_, sender)| sender.clone())
    }

    fn remove(&self, label: &PeerId, token: u64) {
        let mut senders = self.senders.lock().unwrap();
        let owned = senders
            .get(label)
            .map(|(stored, _)| *stored == token)
            .unwrap_or(false);
        if owned {
            senders.remove(label);
        }
    }
}

/// A running transport node. Dropping it does not guarantee task cleanup; call [`Node::shutdown`]
/// for a clean shutdown.
pub struct Node {
    local_addr: SocketAddr,
    kernel: Arc<Mutex<GossipKernel>>,
    peers: Arc<PeerRegistry>,
    shutdown: watch::Sender<bool>,
    accept_task: JoinHandle<()>,
    conn_tasks: Mutex<Vec<JoinHandle<()>>>,
    accepted_rx: AsyncMutex<mpsc::Receiver<TcpStream>>,
    max_frame_bytes: usize,
    outbound_queue_capacity: usize,
}

impl Node {
    /// Bind an ephemeral `127.0.0.1` TCP port and start the accept loop.
    pub async fn bind(config: NodeConfig) -> Result<Self, TransportError> {
        config.validate()?;
        let kernel = GossipKernel::new(config.kernel_config()).map_err(TransportError::Kernel)?;
        let listener = TcpListener::bind((LOCALHOST, 0))
            .await
            .map_err(TransportError::Bind)?;
        let local_addr = listener.local_addr().map_err(TransportError::Bind)?;

        let (shutdown, shutdown_rx) = watch::channel(false);
        let (accepted_tx, accepted_rx) = mpsc::channel(config.accepted_queue_capacity);

        let mut accept_shutdown = shutdown_rx.clone();
        let accept_task = tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = accept_shutdown.changed() => break,
                    incoming = listener.accept() => {
                        match incoming {
                            Ok((stream, _peer_addr)) => {
                                if accepted_tx.send(stream).await.is_err() {
                                    break;
                                }
                            }
                            Err(_) => break,
                        }
                    }
                }
            }
        });

        Ok(Self {
            local_addr,
            kernel: Arc::new(Mutex::new(kernel)),
            peers: Arc::new(PeerRegistry::default()),
            shutdown,
            accept_task,
            conn_tasks: Mutex::new(Vec::new()),
            accepted_rx: AsyncMutex::new(accepted_rx),
            max_frame_bytes: config.max_frame_bytes,
            outbound_queue_capacity: config.outbound_queue_capacity,
        })
    }

    /// The ephemeral address this node is listening on.
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    /// Accept the next inbound connection and bind it to an operator-supplied peer label.
    /// Returns once the connection's read/write task has registered its outbound sender.
    pub async fn accept_peer(&self, label: PeerId) -> Result<(), TransportError> {
        let stream = self
            .accepted_rx
            .lock()
            .await
            .recv()
            .await
            .ok_or(TransportError::Shutdown)?;
        self.spawn_connection(label, stream).await
    }

    /// Dial `addr` and bind the outbound connection to an operator-supplied peer label.
    /// Returns once the connection's read/write task has registered its outbound sender.
    pub async fn connect_peer(
        &self,
        label: PeerId,
        addr: SocketAddr,
    ) -> Result<(), TransportError> {
        let stream = TcpStream::connect(addr)
            .await
            .map_err(TransportError::Connect)?;
        self.spawn_connection(label, stream).await
    }

    /// Inject one envelope to a connected peer. No kernel processing happens here: the peer's
    /// own read loop feeds the envelope to its kernel after the frame arrives.
    pub async fn send(&self, peer: &PeerId, envelope: &Envelope) -> Result<(), TransportError> {
        let frame = codec::encode_frame(envelope, self.max_frame_bytes)?;
        let sender = self
            .peers
            .get(peer)
            .ok_or_else(|| TransportError::PeerNotConnected(peer.clone()))?;
        sender
            .send(frame)
            .await
            .map_err(|_| TransportError::PeerDisconnected(peer.clone()))
    }

    /// Return a snapshot of the kernel's replayable delivery trace. Used to observe acceptance,
    /// refusals and fanout decisions without exposing the mutable kernel.
    pub fn kernel_trace(&self) -> DeliveryTrace {
        self.kernel.lock().unwrap().trace().clone()
    }

    /// Signal every task to stop, reap them and close all sockets.
    pub async fn shutdown(self) -> Result<(), TransportError> {
        let _ = self.shutdown.send(true);
        self.accept_task.abort();
        let tasks: Vec<JoinHandle<()>> = self.conn_tasks.lock().unwrap().drain(..).collect();
        for task in tasks {
            let _ = task.await;
        }
        let _ = self.accept_task.await;
        Ok(())
    }

    /// Spawn the reader and writer tasks for one connection, then wait until the outbound
    /// sender is registered. The two tasks are separate so that a `read_frame` future is never
    /// cancelled mid-frame by an outbound write becoming ready (which would corrupt framing).
    async fn spawn_connection(
        &self,
        label: PeerId,
        stream: TcpStream,
    ) -> Result<(), TransportError> {
        let (outbound_tx, mut outbound_rx) = mpsc::channel::<Vec<u8>>(self.outbound_queue_capacity);
        let token = self.peers.register(label.clone(), outbound_tx)?;

        let (mut reader, mut writer) = split(stream);
        let max_frame_bytes = self.max_frame_bytes;

        // Writer: drains outbound frames. It ends when the registry entry is dropped (the read
        // task ended and removed it) or when the node shuts down.
        let mut writer_shutdown = self.shutdown.subscribe();
        let writer_task = tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = writer_shutdown.changed() => break,
                    outgoing = outbound_rx.recv() => {
                        match outgoing {
                            Some(frame) => {
                                if writer.write_all(&frame).await.is_err() {
                                    break;
                                }
                            }
                            None => break,
                        }
                    }
                }
            }
        });

        // Reader: the only competitor to a frame read is shutdown, so a partial frame is never
        // discarded by an unrelated channel becoming ready.
        let (registered_tx, registered_rx) = oneshot::channel::<()>();
        let reader_task = {
            let kernel = Arc::clone(&self.kernel);
            let peers = Arc::clone(&self.peers);
            let mut reader_shutdown = self.shutdown.subscribe();
            let conn_label = label.clone();

            tokio::spawn(async move {
                // Registration is already in the shared registry; tell the caller it is live.
                let _ = registered_tx.send(());

                loop {
                    tokio::select! {
                        _ = reader_shutdown.changed() => break,
                        incoming = codec::read_frame(&mut reader, max_frame_bytes) => {
                            match incoming {
                                Ok((envelope, raw)) => {
                                    let delivered = {
                                        let mut kernel = kernel.lock().unwrap();
                                        kernel.handle(&envelope)
                                    };
                                    if let Ok(delivered) = delivered {
                                        // Forward the accepted envelope verbatim to every
                                        // fanout peer that has a live connection. Refusals are
                                        // recorded by the kernel and are never forwarded.
                                        for target in delivered.fanout {
                                            if let Some(sender) = peers.get(&target) {
                                                let _ = sender.send(raw.clone()).await;
                                            }
                                        }
                                    }
                                }
                                Err(_) => break, // malformed frame or EOF: fail closed
                            }
                        }
                    }
                }
                // Dropping the registry entry closes the writer's channel, ending that task.
                peers.remove(&conn_label, token);
            })
        };

        {
            let mut tasks = self.conn_tasks.lock().unwrap();
            tasks.push(writer_task);
            tasks.push(reader_task);
        }
        registered_rx.await.map_err(|_| TransportError::Shutdown)?;
        Ok(())
    }
}
