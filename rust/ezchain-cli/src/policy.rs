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
    use super::{refusal_for, Language, Refusal, RefusalCategory, LAYERS, REFUSED_VERBS};

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
