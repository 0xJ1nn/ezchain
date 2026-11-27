//! # ezchain-cli
//!
//! The shipped, read-only operator CLI for ezCHAIN. ADR-0050 assigns the shipped CLI to
//! Rust; this crate is that binary's implementation.
//!
//! It is deliberately small and safe: a typed argument parser, a set of read-only report
//! commands, and a policy gate that REFUSES every operator verb that could touch a database,
//! a key, a venue or money. A refusal is a typed error that names the category and states
//! that live mode is owner-blocked.
//!
//! There are no external crates, no network calls and no file I/O. The only I/O is stdout
//! and stderr in `main.rs`; everything else is a pure function over `&[String]`, which is
//! what makes the parser the test oracle.

#![forbid(unsafe_code)]

pub mod args;
pub mod commands;
pub mod policy;

use args::{parse, ParseError};
use policy::Refusal;

/// A typed error returned by [`run`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CliError {
    /// The command line could not be parsed.
    Parse(ParseError),
    /// A recognised operator verb was refused by policy.
    Refused(Refusal),
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CliError::Parse(error) => write!(f, "{error}"),
            CliError::Refused(refusal) => write!(f, "{refusal}"),
        }
    }
}

/// Parse and execute a command line, returning the exact lines to print on stdout.
///
/// `args` is the argument vector *after* the program name (like
/// `std::env::args().skip(1)`). This pure function is the end-to-end oracle used by the
/// tests; [`main`](../main/index.html) only prints the lines and sets the exit code.
pub fn run(args: &[String]) -> Result<Vec<String>, CliError> {
    let command = parse(args).map_err(CliError::Parse)?;
    commands::execute(&command).map_err(CliError::Refused)
}

#[cfg(test)]
mod tests {
    use super::{run, CliError};
    use crate::args::ParseError;
    use crate::policy::{Refusal, RefusalCategory};

    fn argv(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    #[test]
    fn run_renders_every_read_only_command() {
        assert_eq!(
            run(&argv(&["version"])).unwrap(),
            crate::commands::version()
        );
        assert_eq!(run(&argv(&["mode"])).unwrap(), crate::commands::mode());
        assert_eq!(
            run(&argv(&["contracts"])).unwrap(),
            crate::commands::contracts(None)
        );
        assert_eq!(
            run(&argv(&["contracts", "--layer", "core"])).unwrap(),
            crate::commands::contracts(Some("core"))
        );
        assert_eq!(
            run(&argv(&["contracts", "--all"])).unwrap(),
            crate::commands::contracts_all()
        );
        assert_eq!(
            run(&argv(&["policy"])).unwrap(),
            crate::commands::policy(None)
        );
        assert_eq!(
            run(&argv(&["policy", "--layer", "rust"])).unwrap(),
            crate::commands::policy(Some("rust"))
        );
        assert_eq!(run(&argv(&["gate"])).unwrap(), crate::commands::gate());
        assert_eq!(run(&argv(&["help"])).unwrap(), crate::commands::help());
    }

    #[test]
    fn run_wraps_parse_errors() {
        assert_eq!(run(&argv(&[])), Err(CliError::Parse(ParseError::NoCommand)));
        assert_eq!(
            run(&argv(&["bogus"])),
            Err(CliError::Parse(ParseError::UnknownCommand("bogus".into())))
        );
    }

    #[test]
    fn run_wraps_policy_refusals() {
        let expected = Refusal {
            verb: "order",
            category: RefusalCategory::Money,
        };
        assert_eq!(
            run(&argv(&["order", "--usd", "5"])),
            Err(CliError::Refused(expected))
        );
    }

    #[test]
    fn run_refuse_command_returns_the_existing_typed_refusal() {
        for verb in ["order", "live", "sign", "quote", "backup"] {
            let expected = crate::policy::refusal_for(verb).unwrap();
            assert_eq!(
                run(&argv(&["refuse", verb])),
                Err(CliError::Refused(expected)),
                "verb {verb}"
            );
        }
    }

    #[test]
    fn run_wraps_new_parse_error_branches() {
        assert_eq!(
            run(&argv(&["contracts", "--layer"])),
            Err(CliError::Parse(ParseError::MissingLayerValue))
        );
        assert_eq!(
            run(&argv(&["policy", "--layer"])),
            Err(CliError::Parse(ParseError::MissingPolicyLayerValue))
        );
        assert_eq!(
            run(&argv(&["policy", "--layer", "nope"])),
            Err(CliError::Parse(ParseError::UnknownPolicyLayer(
                "nope".into()
            )))
        );
        assert_eq!(
            run(&argv(&["refuse"])),
            Err(CliError::Parse(ParseError::MissingRefusalVerb))
        );
        assert_eq!(
            run(&argv(&["refuse", "version"])),
            Err(CliError::Parse(ParseError::UnknownRefusalVerb(
                "version".into()
            )))
        );
    }

    #[test]
    fn cli_error_display_delegates_to_the_typed_error() {
        let parse = CliError::Parse(ParseError::NoCommand);
        assert!(parse.to_string().contains("no command"));

        let refused = CliError::Refused(Refusal {
            verb: "sign",
            category: RefusalCategory::Keys,
        });
        let message = refused.to_string();
        assert!(message.contains("live mode is owner-blocked"), "{message}");
        assert!(message.contains("'sign'"), "{message}");
    }
}
