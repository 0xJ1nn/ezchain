//! The documented policy this CLI reports and enforces.
//!
//! Sources: ADR-0050 (language map, live stays owner-blocked), ADR-0035 (execution mode is
//! server-derived; live requires a signed gate record and the key-holding executor is
//! unshipped), and `docs/CONTRACT.md` (the money-path invariants). Nothing here performs
//! I/O or reaches any of the things the policy talks about — it only names them.

/// The language that owns a layer under ADR-0050.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Rust,
    Python,
    TypeScript,
}

impl Language {
    /// The label printed by the `contracts` command.
    pub fn as_str(self) -> &'static str {
        match self {
            Language::Rust => "Rust",
            Language::Python => "Python",
            Language::TypeScript => "TypeScript",
        }
    }
}

/// One row of the ADR-0050 language map.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LayerContract {
    /// Short key used by `contracts --layer <key>`.
    pub key: &'static str,
    /// The layer as ADR-0050 names it.
    pub layer: &'static str,
    /// The language that owns the layer.
    pub language: Language,
}

/// The normative language map (ADR-0050, "Decision — the language map is normative").
pub const LAYERS: &[LayerContract] = &[
    LayerContract {
        key: "core",
        layer: "Core engine, deterministic decision/scheduling kernels",
        language: Language::Rust,
    },
    LayerContract {
        key: "p2p",
        layer: "P2P networking",
        language: Language::Rust,
    },
    LayerContract {
        key: "consensus",
        layer: "Consensus / validation",
        language: Language::Rust,
    },
    LayerContract {
        key: "rpc",
        layer: "RPC server",
        language: Language::Rust,
    },
    LayerContract {
        key: "execution",
        layer: "Transaction execution",
        language: Language::Rust,
    },
    LayerContract {
        key: "cli",
        layer: "CLI and operator tooling shipped to users",
        language: Language::Rust,
    },
    LayerContract {
        key: "indexer",
        layer: "Indexer / analytics",
        language: Language::Python,
    },
    LayerContract {
        key: "ai",
        layer: "AI / ML components, agent harness integration",
        language: Language::Python,
    },
    LayerContract {
        key: "strategy",
        layer: "Strategy plugins",
        language: Language::Python,
    },
    LayerContract {
        key: "ui",
        layer: "Dashboard and all product UI",
        language: Language::TypeScript,
    },
];

/// One section of the ADR-0052 ownership policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PolicySection {
    /// Short key used by `policy --layer <key>`.
    pub key: &'static str,
    /// Title printed above the section's items.
    pub title: &'static str,
    /// The policy points, verbatim in meaning from ADR-0052.
    pub items: &'static [&'static str],
}

/// The ADR-0052 hybrid-architecture ownership view. This is documentation the CLI reports,
/// not a switch: no command here changes which language owns a layer.
pub const POLICY_SECTIONS: &[PolicySection] = &[
    PolicySection {
        key: "rust",
        title: "Rust owns the execution and trust boundary (ADR-0052)",
        items: &[
            "High-performance execution and processing",
            "Networking, P2P and WebSockets",
            "Transaction processing and validation",
            "Concurrency and parallel execution",
            "Memory management and storage",
            "Cryptographic operations",
            "Performance-critical algorithms",
            "Security-critical components",
        ],
    },
    PolicySection {
        key: "python",
        title: "Python owns the application and intelligence layer (ADR-0052)",
        items: &[
            "Business logic and orchestration",
            "AI/ML models and inference",
            "Analytics and data processing",
            "Strategy development and experimentation",
            "Automation and scripting",
            "Monitoring and reporting",
            "APIs and integrations",
            "Prototyping and developer tools",
        ],
    },
    PolicySection {
        key: "overlap",
        title: "Interpretation of the one overlap (ADR-0052)",
        items: &[
            "Rust owns the node-facing RPC and WebSocket transport (the wire protocol)",
            "Python owns the application-level HTTP API and the product-facing WebSocket gateway in apps/api",
            "Python owns third-party integrations that compose those calls for the product",
            "Only the node-facing P2P/WebSocket transport is Rust; the product gateway stays Python",
        ],
    },
    PolicySection {
        key: "integration",
        title: "Integration contract (ADR-0052)",
        items: &[
            "1. PyO3 in-process; gRPC or IPC across services; a typed, versioned boundary",
            "2. Performance- and security-sensitive logic in Rust; evolving intelligence in Python",
            "3. Preserve working code; migrate bottlenecks with recorded behavioural parity as the gate",
            "4. Clear interfaces; independent testing, benchmarking and deployment per crate",
        ],
    },
];

/// The four steps of `record → diff → gate → bench`, the definition of done for a ported unit
/// (ADR-0050 rule 2, `rust/README.md` "Gate for a port").
pub const PORT_GATES: &[(&str, &str)] = &[
    (
        "record",
        "capture the Python original's cases, including failures",
    ),
    (
        "diff",
        "make the Rust implementation match every recorded case",
    ),
    (
        "gate",
        "cargo fmt --check, cargo clippy, cargo test and the behaviour gate pass",
    ),
    (
        "bench",
        "benchmark the port; a small per-call FFI port is usually slower",
    ),
];

/// The Rust gate commands (ADR-0050 rule 6, `rust/README.md`).
pub const RUST_GATE_COMMANDS: &[&str] = &[
    "cargo fmt --all -- --check",
    "cargo clippy --workspace --all-targets -- -D warnings",
    "cargo test --workspace",
];

/// The local gate that wraps every tool (ADR-0050 rule 6).
pub const LOCAL_GATE_COMMAND: &str = "bash tools/ci.sh";

/// Why a recognised operator verb is refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefusalCategory {
    /// Would read or write a database.
    Database,
    /// Would read, import or sign with key material.
    Keys,
    /// Would reach a trading venue or provider.
    Venue,
    /// Would place an order or move money.
    Money,
    /// Would arm or switch execution mode to live.
    LiveMode,
}

impl RefusalCategory {
    /// A short, operator-facing reason for the refusal.
    pub fn reason(self) -> &'static str {
        match self {
            RefusalCategory::Database => "would touch a database",
            RefusalCategory::Keys => "would touch key material",
            RefusalCategory::Venue => "would reach a trading venue",
            RefusalCategory::Money => "would move money",
            RefusalCategory::LiveMode => "would enable live execution",
        }
    }
}

/// A typed policy refusal for one operator verb.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Refusal {
    /// The verb that was refused.
    pub verb: &'static str,
    /// Why it was refused.
    pub category: RefusalCategory,
}

impl std::fmt::Display for Refusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "refused: '{verb}' {reason}\nlive mode is owner-blocked (ADR-0035/0050); ezchain-cli is read-only and never reaches a database, a key, a venue or money",
            verb = self.verb,
            reason = self.category.reason(),
        )
    }
}

/// Operator verbs from `tools/ops`, `tools/stress` and the executor that this read-only CLI
/// recognises only in order to refuse. The mapping is policy, not a parser detail.
pub const REFUSED_VERBS: &[(&str, RefusalCategory)] = &[
    ("backup", RefusalCategory::Database),
    ("restore", RefusalCategory::Database),
    ("verify", RefusalCategory::Database),
    ("rehearse", RefusalCategory::Database),
    ("drop", RefusalCategory::Database),
    ("drill", RefusalCategory::Database),
    ("chain", RefusalCategory::Database),
    ("sign", RefusalCategory::Keys),
    ("key", RefusalCategory::Keys),
    ("quote", RefusalCategory::Venue),
    ("order", RefusalCategory::Money),
    ("place-order", RefusalCategory::Money),
    ("trade", RefusalCategory::Money),
    ("live", RefusalCategory::LiveMode),
];

/// Look up a refused verb. `None` means it is not a recognised operator verb.
pub fn refusal_for(verb: &str) -> Option<Refusal> {
    REFUSED_VERBS
        .iter()
        .find(|(name, _)| *name == verb)
        .map(|(name, category)| Refusal {
            verb: name,
            category: *category,
        })
}

#[cfg(test)]
mod tests {
    use super::{
        refusal_for, Language, Refusal, RefusalCategory, LAYERS, LOCAL_GATE_COMMAND,
        POLICY_SECTIONS, PORT_GATES, REFUSED_VERBS, RUST_GATE_COMMANDS,
    };

    #[test]
    fn language_labels() {
        assert_eq!(Language::Rust.as_str(), "Rust");
        assert_eq!(Language::Python.as_str(), "Python");
        assert_eq!(Language::TypeScript.as_str(), "TypeScript");
    }

    #[test]
    fn language_map_matches_adr_0050() {
        let expected = [
            ("core", Language::Rust),
            ("p2p", Language::Rust),
            ("consensus", Language::Rust),
            ("rpc", Language::Rust),
            ("execution", Language::Rust),
            ("cli", Language::Rust),
            ("indexer", Language::Python),
            ("ai", Language::Python),
            ("strategy", Language::Python),
            ("ui", Language::TypeScript),
        ];
        assert_eq!(LAYERS.len(), expected.len());
        for (contract, (key, language)) in LAYERS.iter().zip(expected.iter()) {
            assert_eq!(contract.key, *key);
            assert_eq!(contract.language, *language);
        }
        assert_eq!(
            LAYERS
                .iter()
                .filter(|c| c.language == Language::Rust)
                .count(),
            6
        );
        assert_eq!(
            LAYERS
                .iter()
                .filter(|c| c.language == Language::Python)
                .count(),
            3
        );
        assert_eq!(
            LAYERS
                .iter()
                .filter(|c| c.language == Language::TypeScript)
                .count(),
            1
        );
    }

    #[test]
    fn layer_keys_are_unique() {
        for (index, contract) in LAYERS.iter().enumerate() {
            assert!(
                !LAYERS[..index]
                    .iter()
                    .any(|other| other.key == contract.key),
                "duplicate layer key {}",
                contract.key
            );
        }
    }

    #[test]
    fn adr_0052_policy_sections_have_unique_keys_and_content() {
        let expected: &[&str] = &["rust", "python", "overlap", "integration"];
        assert_eq!(
            POLICY_SECTIONS.iter().map(|s| s.key).collect::<Vec<_>>(),
            expected
        );
        for section in POLICY_SECTIONS {
            assert!(!section.title.is_empty());
            assert!(!section.items.is_empty(), "section {}", section.key);
            assert!(
                section.items.iter().all(|item| !item.is_empty()),
                "section {}",
                section.key
            );
            for other in POLICY_SECTIONS {
                if other.key == section.key {
                    continue;
                }
                assert!(
                    section.items.iter().all(|item| !other.items.contains(item)),
                    "duplicate policy item across {} and {}",
                    section.key,
                    other.key
                );
            }
        }
    }

    #[test]
    fn documented_gates_are_in_the_record_diff_gate_bench_order() {
        let names: Vec<&str> = PORT_GATES.iter().map(|(name, _)| *name).collect();
        assert_eq!(names, ["record", "diff", "gate", "bench"]);
        assert!(PORT_GATES.iter().all(|(_, detail)| !detail.is_empty()));
    }

    #[test]
    fn rust_and_local_gate_commands_are_present() {
        assert_eq!(RUST_GATE_COMMANDS.len(), 3);
        assert!(RUST_GATE_COMMANDS[0].starts_with("cargo fmt"));
        assert!(RUST_GATE_COMMANDS[1].starts_with("cargo clippy"));
        assert!(RUST_GATE_COMMANDS[2].starts_with("cargo test"));
        assert_eq!(LOCAL_GATE_COMMAND, "bash tools/ci.sh");
    }

    #[test]
    fn refusal_reasons_are_distinct_and_non_empty() {
        let categories = [
            RefusalCategory::Database,
            RefusalCategory::Keys,
            RefusalCategory::Venue,
            RefusalCategory::Money,
            RefusalCategory::LiveMode,
        ];
        let mut reasons = std::collections::BTreeSet::new();
        for category in categories {
            assert!(!category.reason().is_empty());
            assert!(reasons.insert(category.reason()));
        }
        assert_eq!(reasons.len(), 5);
    }

    #[test]
    fn refusal_lookup_covers_every_registered_verb() {
        assert_eq!(REFUSED_VERBS.len(), 14);
        for (verb, category) in REFUSED_VERBS {
            assert_eq!(
                refusal_for(verb),
                Some(Refusal {
                    verb,
                    category: *category,
                })
            );
        }
        assert_eq!(refusal_for("version"), None);
        assert_eq!(refusal_for("bogus"), None);
    }

    #[test]
    fn every_refusal_message_names_owner_blocked_live_mode() {
        for (verb, category) in REFUSED_VERBS {
            let message = Refusal {
                verb,
                category: *category,
            }
            .to_string();
            assert!(message.contains("live mode is owner-blocked"), "{message}");
            assert!(message.contains(&format!("'{verb}'")), "{message}");
            assert!(message.contains(category.reason()), "{message}");
        }
    }
}
