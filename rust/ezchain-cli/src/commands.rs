//! Pure renderers for the read-only commands. No I/O here: each function returns the exact
//! lines `main` will print, so the tests compare strings rather than capturing a terminal.

use crate::args::Command;
use crate::policy::{Refusal, LAYERS};

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

/// True when `key` is one of the layer keys in the ADR-0050 map.
pub fn is_known_layer(key: &str) -> bool {
    LAYERS.iter().any(|contract| contract.key == key)
}

/// Print usage.
pub fn help() -> Vec<String> {
    vec![
        "ezchain-cli — read-only operator CLI".to_string(),
        String::new(),
        "usage: ezchain-cli <command> [options]".to_string(),
        String::new(),
        "commands:".to_string(),
        "  version                    print the CLI version".to_string(),
        "  mode                        report the execution mode (always PAPER)".to_string(),
        "  contracts [--layer <key>]  report the ADR-0050 language map".to_string(),
        "  help                        print this message".to_string(),
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
        Command::Help => Ok(help()),
        Command::Refused(refusal) => Err(*refusal),
    }
}

#[cfg(test)]
mod tests {
    use super::{contracts, execute, help, is_known_layer, mode, version};
    use crate::args::Command;
    use crate::policy::{Refusal, RefusalCategory, LAYERS};

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
        for command in ["version", "mode", "contracts", "help"] {
            assert!(joined.contains(command), "{joined}");
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
