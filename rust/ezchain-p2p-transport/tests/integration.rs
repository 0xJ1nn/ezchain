//! Real, loopback-only integration tests for the transport. No external network is used.

use std::time::Duration;

use ezchain_p2p::{
    envelope::{envelope_id, Envelope, Limits, SCHEMA},
    kernel::{GossipKernel, KernelConfig},
    peer::ValidatorSet,
    trace::DeliveryEvent,
};
use ezchain_p2p_transport::{Node, NodeConfig};
use tokio::io::AsyncWriteExt;

fn limits() -> Limits {
    Limits {
        max_envelope_bytes: 1024,
        max_payload_bytes: 256,
    }
}

fn config(self_id: &str, fanout: usize) -> NodeConfig {
    let validators = ValidatorSet::new(
        "set-1",
        ["node-a", "node-b", "node-c"].iter().map(|s| s.to_string()),
        3,
    )
    .unwrap();
    NodeConfig {
        self_id: self_id.to_string(),
        validators,
        limits: limits(),
        dedup_capacity: 16,
        queue_capacity: 16,
        fanout,
        max_frame_bytes: 2048,
        outbound_queue_capacity: 16,
        accepted_queue_capacity: 4,
    }
}

fn envelope(sender: &str, seq: u64) -> Envelope {
    let mut envelope = Envelope {
        schema: SCHEMA.to_string(),
        kind: "tape_event".to_string(),
        sender: sender.to_string(),
        set_version: "set-1".to_string(),
        seq,
        payload: vec![seq as u8, 2, 3],
        timestamp: 1_700_000_000 + seq,
        digest: 0,
    };
    envelope.digest = envelope_id(&envelope);
    envelope
}

fn oracle(self_id: &str, fanout: usize) -> GossipKernel {
    let cfg = config(self_id, fanout);
    GossipKernel::new(KernelConfig {
        self_id: cfg.self_id,
        validators: cfg.validators,
        limits: cfg.limits,
        dedup_capacity: cfg.dedup_capacity,
        queue_capacity: cfg.queue_capacity,
        fanout: cfg.fanout,
    })
    .unwrap()
}

async fn wait_until(label: &str, predicate: impl Fn() -> bool) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    while !predicate() {
        assert!(
            tokio::time::Instant::now() < deadline,
            "timed out waiting for {label}"
        );
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

/// The required end-to-end case: two ephemeral nodes, one connection, one valid envelope, an
/// accepted trace with the expected digest, then a clean shutdown.
#[tokio::test]
async fn two_nodes_deliver_one_valid_envelope_and_shut_down_cleanly() {
    let a = Node::bind(config("node-a", 1)).await.unwrap();
    let b = Node::bind(config("node-b", 1)).await.unwrap();
    assert_ne!(a.local_addr(), b.local_addr());
    assert_eq!(a.local_addr().ip(), b.local_addr().ip());

    a.connect_peer("node-b".to_string(), b.local_addr())
        .await
        .unwrap();
    b.accept_peer("node-a".to_string()).await.unwrap();

    let env = envelope("node-a", 1);
    a.send(&"node-b".to_string(), &env).await.unwrap();

    wait_until("b accepts one envelope", || {
        b.kernel_trace().events().len() == 1
    })
    .await;

    let trace = b.kernel_trace();
    let summary = trace.replay();
    let expected_id = envelope_id(&env);

    // The receiving kernel accepted exactly this envelope with no refusals.
    assert_eq!(summary.accepted_ids, vec![expected_id]);
    assert!(summary.refused.is_empty());
    match &trace.events()[0] {
        DeliveryEvent::Accepted {
            id,
            sender,
            seq,
            fanout,
        } => {
            assert_eq!(*id, expected_id);
            assert_eq!(sender, "node-a");
            assert_eq!(*seq, 1);
            assert_eq!(fanout, &vec!["node-b".to_string()]);
        }
        other => panic!("expected an Accepted event, got {other:?}"),
    }
    // The fanout projection names the same peer the kernel selected.
    assert_eq!(summary.forwarded_to, vec!["node-b".to_string()]);

    // Independent oracle: an in-memory kernel fed the same envelope yields the same replay
    // digest, so the receiving node's trace is exactly the expected one.
    let mut expected_kernel = oracle("node-b", 1);
    expected_kernel.handle(&env).unwrap();
    assert_eq!(summary.digest, expected_kernel.trace().replay().digest);

    a.shutdown().await.unwrap();
    b.shutdown().await.unwrap();
}

/// Accepted envelopes are written to a connected peer named by the kernel's fanout selection.
#[tokio::test]
async fn accepted_envelopes_are_forwarded_to_connected_fanout_peers() {
    let a = Node::bind(config("node-a", 2)).await.unwrap();
    let b = Node::bind(config("node-b", 2)).await.unwrap();
    let c = Node::bind(config("node-c", 2)).await.unwrap();

    // Topology: A -> B -> C, with all three ids in the same operator-supplied validator set.
    a.connect_peer("node-b".to_string(), b.local_addr())
        .await
        .unwrap();
    b.accept_peer("node-a".to_string()).await.unwrap();
    b.connect_peer("node-c".to_string(), c.local_addr())
        .await
        .unwrap();
    c.accept_peer("node-b".to_string()).await.unwrap();

    let env = envelope("node-a", 7);
    a.send(&"node-b".to_string(), &env).await.unwrap();
    // B accepts and its fanout (sorted [node-b, node-c] excluding the sender node-a) forwards
    // to the connected peer labelled node-c.
    wait_until("c accepts B's forwarded envelope", || {
        c.kernel_trace().replay().accepted_ids.len() == 1
    })
    .await;
    assert_eq!(
        c.kernel_trace().replay().accepted_ids,
        vec![envelope_id(&env)]
    );

    a.shutdown().await.unwrap();
    b.shutdown().await.unwrap();
    c.shutdown().await.unwrap();
}

/// A malformed frame closes only its own connection; a later valid peer still works and the
/// node does not panic.
#[tokio::test]
async fn a_malformed_frame_closes_only_its_connection() {
    let b = Node::bind(config("node-b", 1)).await.unwrap();

    let mut raw = tokio::net::TcpStream::connect(b.local_addr())
        .await
        .unwrap();
    b.accept_peer("raw-peer".to_string()).await.unwrap();
    raw.write_all(&0u32.to_le_bytes()).await.unwrap(); // zero length -> typed CodecError

    let a = Node::bind(config("node-a", 1)).await.unwrap();
    a.connect_peer("node-b".to_string(), b.local_addr())
        .await
        .unwrap();
    b.accept_peer("node-a".to_string()).await.unwrap();

    let env = envelope("node-a", 3);
    a.send(&"node-b".to_string(), &env).await.unwrap();
    wait_until("b accepts after the malformed frame", || {
        b.kernel_trace().events().len() == 1
    })
    .await;
    assert_eq!(
        b.kernel_trace().replay().accepted_ids,
        vec![envelope_id(&env)]
    );

    a.shutdown().await.unwrap();
    b.shutdown().await.unwrap();
}

/// Sending to an unconnected label is a typed error, not a panic or a silent success.
#[tokio::test]
async fn sending_to_an_unconnected_peer_is_a_typed_error() {
    let a = Node::bind(config("node-a", 1)).await.unwrap();
    let error = a
        .send(&"node-b".to_string(), &envelope("node-a", 1))
        .await
        .unwrap_err();
    assert!(matches!(
        error,
        ezchain_p2p_transport::TransportError::PeerNotConnected(_)
    ));
    a.shutdown().await.unwrap();
}
