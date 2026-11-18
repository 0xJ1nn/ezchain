//! Pure renderers for the read-only commands. No I/O here: each function returns the exact
//! lines `main` will print, so the tests compare strings rather than capturing a terminal.

use crate::args::Command;
use crate::policy::{
    Refusal, LAYERS, LOCAL_GATE_COMMAND, POLICY_SECTIONS, PORT_GATES, RUST_GATE_COMMANDS,
};

/// Print the CLI version (from the crate manifest, so it cannot drift from the build).
pub fn version() -> Vec<String> {
    vec![format!("ezchain-cli {}", env!("CARGO_PKG_VERSION"))]
}

/// Report the execution mode. A live start is owner-blocked, so this binary can only ever
/// report PAPER; the mode is not a flag it accepts (there is no `--mode live`).
pub fn mode() -> Vec<String> {
    vec![
        "mode: PAPER".to_string(),
        "source: ADR-0035 / ADR-0050 (server-derived; live is owner-blocked)".to_string(),
        "note: ezchain-cli is read-only; it never reaches a database, a key, a venue or money"
            .to_string(),
    ]
}

/// Report the ADR-0050 language map, optionally filtered by layer key.
pub fn contracts(filter: Option<&str>) -> Vec<String> {
    let mut lines = vec![match filter {
        Some(key) => format!("language map (ADR-0050), layer={key}:"),
        None => "language map (ADR-0050):".to_string(),
    }];
    for contract in LAYERS {
        if filter.is_some_and(|key| key != contract.key) {
            continue;
        }
        lines.push(format!(
            "  {:<10} -> {}",
            contract.key,
            contract.language.as_str()
        ));
    }
    lines
}

/// The full ADR-0050 language map as an aligned table, including the layer descriptions that
/// `contracts` (the short form) omits.
pub fn contracts_all() -> Vec<String> {
    let key_width = LAYERS.iter().map(|c| c.key.len()).max().unwrap_or(0);
    let layer_width = LAYERS.iter().map(|c| c.layer.len()).max().unwrap_or(0);
    let mut lines = vec![
        "language map (ADR-0050), all layers:".to_string(),
        format!(
            "  {:<kw$}  {:<lw$}  {}",
            "key",
            "layer",
            "language",
            kw = key_width,
            lw = layer_width
        ),
    ];
    for contract in LAYERS {
        lines.push(format!(
            "  {:<kw$}  {:<lw$}  {}",
            contract.key,
            contract.layer,
            contract.language.as_str(),
            kw = key_width,
            lw = layer_width
        ));
    }
    lines
}

/// True when `key` is one of the layer keys in the ADR-0050 map.
pub fn is_known_layer(key: &str) -> bool {
    LAYERS.iter().any(|contract| contract.key == key)
}

/// Report the ADR-0052 ownership policy, optionally one named section. This is a read-only
/// view of the documented decision; it never changes which language owns a layer.
pub fn policy(filter: Option<&str>) -> Vec<String> {
    let mut lines = vec![match filter {
        Some(key) => format!("hybrid architecture policy (ADR-0052), layer={key}:"),
        None => "hybrid architecture policy (ADR-0052):".to_string(),
    }];
    for section in POLICY_SECTIONS {
        if filter.is_some_and(|key| key != section.key) {
            continue;
        }
        lines.push(String::new());
        lines.push(section.title.to_string());
        for item in section.items {
            lines.push(format!("  - {item}"));
        }
    }
    lines
}

/// True when `key` names a section of the ADR-0052 policy view.
pub fn is_known_policy_layer(key: &str) -> bool {
    POLICY_SECTIONS.iter().any(|section| section.key == key)
}

/// List the required port gates from the documented policy (ADR-0050 rule 2, rule 6 and
/// `rust/README.md`). Read-only: it names the gates, it does not run them.
pub fn gate() -> Vec<String> {
    let mut lines = vec!["port gates (ADR-0050 rule 2 / rust/README.md):".to_string()];
    for (step, detail) in PORT_GATES {
        lines.push(format!("  {step:<6} {detail}"));
    }
    lines.push(String::new());
    lines.push("rust gate:".to_string());
    for command in RUST_GATE_COMMANDS {
        lines.push(format!("  {command}"));
    }
    lines.push(String::new());
    lines.push("local gate:".to_string());
    lines.push(format!("  {LOCAL_GATE_COMMAND}"));
    lines
}

/// Print usage.
pub fn help() -> Vec<String> {
    vec![
        "ezchain-cli — read-only operator CLI".to_string(),
        String::new(),
        "usage: ezchain-cli <command> [options]".to_string(),
        String::new(),
        "commands:".to_string(),
        "  version                     print the CLI version".to_string(),
        "  mode                         report the execution mode (always PAPER)".to_string(),
        "  contracts                    short ADR-0050 language map (key -> language)".to_string(),
        "  contracts --all              full ADR-0050 table with layer descriptions".to_string(),
        "  contracts --layer <key>      one language-map row by layer key".to_string(),
        "  policy [--layer <key>]       ADR-0052 ownership view (rust|python|overlap|integration)"
            .to_string(),
        "  gate                         required port gates from the documented policy".to_string(),
        "  refuse <verb>                show the typed refusal for an operator verb (exit 2)"
            .to_string(),
        "  help                         print this message".to_string(),
        String::new(),
        "Operator verbs that touch a database, key, venue or money are refused with a typed"
            .to_string(),
        "error; live mode is owner-blocked (ADR-0035/0050).".to_string(),
    ]
}

/// Execute a parsed command, or return its typed policy refusal.
pub fn execute(command: &Command) -> Result<Vec<String>, Refusal> {
    match command {
        Command::Version => Ok(version()),
        Command::Mode => Ok(mode()),
        Command::Contracts(filter) => Ok(contracts(filter.as_deref())),
        Command::ContractsAll => Ok(contracts_all()),
        Command::Policy(filter) => Ok(policy(filter.as_deref())),
        Command::Gate => Ok(gate()),
        Command::Help => Ok(help()),
        Command::Refused(refusal) => Err(*refusal),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        contracts, contracts_all, execute, gate, help, is_known_layer, is_known_policy_layer, mode,
        policy, version,
    };
    use crate::args::Command;
    use crate::policy::{
        Refusal, RefusalCategory, LAYERS, LOCAL_GATE_COMMAND, POLICY_SECTIONS, PORT_GATES,
        RUST_GATE_COMMANDS,
    };

    #[test]
    fn version_reports_the_crate_version() {
        let lines = version();
        assert_eq!(lines.len(), 1);
        assert_eq!(
            lines[0],
            format!("ezchain-cli {}", env!("CARGO_PKG_VERSION"))
        );
    }

    #[test]
    fn mode_never_reports_live() {
        let lines = mode();
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0], "mode: PAPER");
        assert!(lines.iter().all(|line| !line.contains("mode: LIVE")));
        assert!(lines.iter().any(|line| line.contains("owner-blocked")));
    }

    #[test]
    fn contracts_reports_every_layer() {
        let lines = contracts(None);
        assert_eq!(lines.len(), LAYERS.len() + 1);
        assert_eq!(lines[0], "language map (ADR-0050):");
        for (line, contract) in lines[1..].iter().zip(LAYERS.iter()) {
            assert!(line.contains(contract.key), "{line}");
            assert!(line.contains(contract.language.as_str()), "{line}");
        }
        assert_eq!(
            lines.iter().filter(|line| line.contains("-> Rust")).count(),
            6
        );
        assert_eq!(
            lines
                .iter()
                .filter(|line| line.contains("-> Python"))
                .count(),
            3
        );
        assert_eq!(
            lines
                .iter()
                .filter(|line| line.contains("-> TypeScript"))
                .count(),
            1
        );
    }

    #[test]
    fn contracts_filter_selects_one_layer() {
        let lines = contracts(Some("indexer"));
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0], "language map (ADR-0050), layer=indexer:");
        assert!(lines[1].contains("indexer"));
        assert!(lines[1].contains("Python"));

        let ui = contracts(Some("ui"));
        assert_eq!(ui.len(), 2);
        assert!(ui[1].contains("TypeScript"));
    }

    #[test]
    fn contracts_all_is_an_aligned_table_with_every_layer_description() {
        let lines = contracts_all();
        assert_eq!(lines[0], "language map (ADR-0050), all layers:");
        assert!(lines[1].contains("key") && lines[1].contains("layer"));
        assert!(lines[1].contains("language"));
        assert_eq!(lines.len(), LAYERS.len() + 2);
        for (line, contract) in lines[2..].iter().zip(LAYERS.iter()) {
            assert!(line.contains(contract.key), "{line}");
            assert!(line.contains(contract.layer), "{line}");
            assert!(line.contains(contract.language.as_str()), "{line}");
        }
        // Every data row is indented the same two spaces, so the table is aligned.
        assert!(lines[2..].iter().all(|line| line.starts_with("  ")));
    }

    #[test]
    fn policy_reports_every_adr_0052_section() {
        let lines = policy(None);
        assert_eq!(lines[0], "hybrid architecture policy (ADR-0052):");
        for section in POLICY_SECTIONS {
            assert!(
                lines.iter().any(|line| line == section.title),
                "{}",
                section.key
            );
            for item in section.items {
                assert!(lines.iter().any(|line| line.contains(item)), "{item}");
            }
        }
        // The overlap section names the node-facing transport boundary.
        let joined = lines.join("\n");
        assert!(joined.contains("node-facing"), "{joined}");
    }

    #[test]
    fn policy_filter_selects_one_section() {
        let lines = policy(Some("python"));
        assert_eq!(
            lines[0],
            "hybrid architecture policy (ADR-0052), layer=python:"
        );
        assert!(lines.len() < policy(None).len());
        assert!(lines
            .iter()
            .any(|line| line.contains("Business logic and orchestration")));
        assert!(!lines
            .iter()
            .any(|line| line.contains("High-performance execution and processing")));
    }

    #[test]
    fn policy_layer_key_lookup() {
        assert!(is_known_policy_layer("rust"));
        assert!(is_known_policy_layer("python"));
        assert!(is_known_policy_layer("overlap"));
        assert!(is_known_policy_layer("integration"));
        assert!(!is_known_policy_layer("core"));
        assert!(!is_known_policy_layer("Rust"));
    }

    #[test]
    fn gate_lists_the_documented_gates_and_commands() {
        let lines = gate();
        let joined = lines.join("\n");
        for (step, detail) in PORT_GATES {
            assert!(joined.contains(step), "{joined}");
            assert!(joined.contains(detail), "{joined}");
        }
        for command in RUST_GATE_COMMANDS {
            assert!(joined.contains(command), "{joined}");
        }
        assert!(joined.contains(LOCAL_GATE_COMMAND), "{joined}");
        assert!(joined.contains("record"), "{joined}");
        assert!(joined.contains("diff"), "{joined}");
        assert!(joined.contains("gate:"), "{joined}");
        assert!(joined.contains("bench"), "{joined}");
    }

    #[test]
    fn layer_key_lookup() {
        assert!(is_known_layer("core"));
        assert!(is_known_layer("ui"));
        assert!(!is_known_layer("nope"));
        assert!(!is_known_layer("Rust"));
    }

    #[test]
    fn help_lists_the_read_only_commands_and_the_policy() {
        let lines = help();
        let joined = lines.join("\n");
        for command in [
            "version",
            "mode",
            "contracts",
            "contracts --all",
            "policy",
            "gate",
            "refuse",
            "help",
        ] {
            assert!(joined.contains(command), "missing {command} in {joined}");
        }
        assert!(joined.contains("owner-blocked"), "{joined}");
    }

    #[test]
    fn execute_dispatches_read_only_commands() {
        assert_eq!(execute(&Command::Version).unwrap(), version());
        assert_eq!(execute(&Command::Mode).unwrap(), mode());
        assert_eq!(execute(&Command::Contracts(None)).unwrap(), contracts(None));
        assert_eq!(
            execute(&Command::Contracts(Some("core".into()))).unwrap(),
            contracts(Some("core"))
        );
        assert_eq!(execute(&Command::ContractsAll).unwrap(), contracts_all());
        assert_eq!(execute(&Command::Policy(None)).unwrap(), policy(None));
        assert_eq!(
            execute(&Command::Policy(Some("rust".into()))).unwrap(),
            policy(Some("rust"))
        );
        assert_eq!(execute(&Command::Gate).unwrap(), gate());
        assert_eq!(execute(&Command::Help).unwrap(), help());
    }

    #[test]
    fn execute_returns_the_typed_refusal() {
        let refusal = Refusal {
            verb: "order",
            category: RefusalCategory::Money,
        };
        assert_eq!(execute(&Command::Refused(refusal)), Err(refusal));
    }
}
