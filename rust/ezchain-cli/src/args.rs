//! Typed argument parser — the oracle for the CLI.
//!
//! `parse` takes the argument vector after the program name and returns either a typed
//! [`Command`] or a typed [`ParseError`]. Recognised operator verbs that policy refuses are
//! parsed into [`Command::Refused`] rather than being reported as unknown commands: refusing
//! a verb is different from not knowing it, and the distinction is part of the contract.

use crate::policy::{refusal_for, Refusal};

/// A parsed, typed command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Print the CLI version.
    Version,
    /// Report the derived execution mode (always `PAPER` for this read-only binary).
    Mode,
    /// Report the ADR-0050 language map, optionally filtered by layer key.
    Contracts(Option<String>),
    /// Print usage.
    Help,
    /// A recognised operator verb that policy refuses to run.
    Refused(Refusal),
}

/// A typed command-line parse error.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// No command was supplied.
    NoCommand,
    /// The first token is not a known command.
    UnknownCommand(String),
    /// A command that takes no argument was given one.
    UnexpectedArgument {
        command: &'static str,
        argument: String,
    },
    /// `contracts --layer` was given without a value.
    MissingLayerValue,
    /// `contracts --layer` was given a key that is not in the ADR-0050 map.
    UnknownLayer(String),
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::NoCommand => write!(f, "error: no command supplied; try 'help'"),
            ParseError::UnknownCommand(verb) => {
                write!(f, "error: unknown command '{verb}'; try 'help'")
            }
            ParseError::UnexpectedArgument { command, argument } => {
                write!(
                    f,
                    "error: '{command}' takes no argument '{argument}'; try 'help'"
                )
            }
            ParseError::MissingLayerValue => {
                write!(
                    f,
                    "error: 'contracts --layer' requires a layer key; try 'help'"
                )
            }
            ParseError::UnknownLayer(key) => {
                write!(
                    f,
                    "error: unknown layer '{key}' for 'contracts --layer'; try 'help'"
                )
            }
        }
    }
}

/// Parse an argument vector (after the program name) into a typed command.
pub fn parse(args: &[String]) -> Result<Command, ParseError> {
    let verb = args.first().ok_or(ParseError::NoCommand)?;
    match verb.as_str() {
        "version" => no_argument("version", args, Command::Version),
        "mode" => no_argument("mode", args, Command::Mode),
        "help" | "-h" | "--help" => no_argument("help", args, Command::Help),
        "contracts" => parse_contracts(args),
        other => refusal_for(other)
            .map(Command::Refused)
            .ok_or_else(|| ParseError::UnknownCommand(other.to_string())),
    }
}

fn no_argument(
    command: &'static str,
    args: &[String],
    value: Command,
) -> Result<Command, ParseError> {
    match args.get(1) {
        Some(extra) => Err(ParseError::UnexpectedArgument {
            command,
            argument: extra.clone(),
        }),
        None => Ok(value),
    }
}

fn parse_contracts(args: &[String]) -> Result<Command, ParseError> {
    match args.len() {
        1 => Ok(Command::Contracts(None)),
        2 => {
            if args[1] == "--layer" {
                Err(ParseError::MissingLayerValue)
            } else {
                Err(ParseError::UnexpectedArgument {
                    command: "contracts",
                    argument: args[1].clone(),
                })
            }
        }
        3 => {
            if args[1] != "--layer" {
                Err(ParseError::UnexpectedArgument {
                    command: "contracts",
                    argument: args[1].clone(),
                })
            } else if !crate::commands::is_known_layer(&args[2]) {
                Err(ParseError::UnknownLayer(args[2].clone()))
            } else {
                Ok(Command::Contracts(Some(args[2].clone())))
            }
        }
        _ => Err(ParseError::UnexpectedArgument {
            command: "contracts",
            argument: args.get(3).cloned().unwrap_or_else(|| args[1].clone()),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::{parse, Command, ParseError};
    use crate::policy::{Refusal, RefusalCategory};

    fn argv(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    #[test]
    fn empty_argv_is_an_error() {
        assert_eq!(parse(&argv(&[])), Err(ParseError::NoCommand));
    }

    #[test]
    fn read_only_verbs_parse() {
        assert_eq!(parse(&argv(&["version"])), Ok(Command::Version));
        assert_eq!(parse(&argv(&["mode"])), Ok(Command::Mode));
        assert_eq!(parse(&argv(&["help"])), Ok(Command::Help));
        assert_eq!(parse(&argv(&["-h"])), Ok(Command::Help));
        assert_eq!(parse(&argv(&["--help"])), Ok(Command::Help));
        assert_eq!(parse(&argv(&["contracts"])), Ok(Command::Contracts(None)));
        assert_eq!(
            parse(&argv(&["contracts", "--layer", "core"])),
            Ok(Command::Contracts(Some("core".into())))
        );
    }

    #[test]
    fn unexpected_arguments_on_no_argument_commands_are_errors() {
        for verb in ["version", "mode", "help"] {
            assert_eq!(
                parse(&argv(&[verb, "extra"])),
                Err(ParseError::UnexpectedArgument {
                    command: if verb == "help" { "help" } else { verb },
                    argument: "extra".into(),
                })
            );
        }
    }

    #[test]
    fn contracts_error_branches() {
        assert_eq!(
            parse(&argv(&["contracts", "--layer"])),
            Err(ParseError::MissingLayerValue)
        );
        assert_eq!(
            parse(&argv(&["contracts", "rust"])),
            Err(ParseError::UnexpectedArgument {
                command: "contracts",
                argument: "rust".into(),
            })
        );
        assert_eq!(
            parse(&argv(&["contracts", "--layer", "nope"])),
            Err(ParseError::UnknownLayer("nope".into()))
        );
        assert_eq!(
            parse(&argv(&["contracts", "--layer", "core", "extra"])),
            Err(ParseError::UnexpectedArgument {
                command: "contracts",
                argument: "extra".into(),
            })
        );
    }

    #[test]
    fn unknown_verbs_are_errors() {
        assert_eq!(
            parse(&argv(&["bogus"])),
            Err(ParseError::UnknownCommand("bogus".into()))
        );
    }

    #[test]
    fn every_refused_verb_parses_as_a_typed_refusal() {
        let cases = [
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
        for (verb, category) in cases {
            let expected = Command::Refused(Refusal { verb, category });
            assert_eq!(parse(&argv(&[verb])), Ok(expected.clone()), "verb {verb}");
            // Refusal is decided by the verb alone; trailing args cannot bypass it.
            assert_eq!(
                parse(&argv(&[verb, "--anything", "value"])),
                Ok(expected),
                "verb {verb} with args"
            );
        }
    }

    #[test]
    fn parse_error_messages_are_clear() {
        assert_eq!(
            ParseError::NoCommand.to_string(),
            "error: no command supplied; try 'help'"
        );
        assert_eq!(
            ParseError::UnknownCommand("x".into()).to_string(),
            "error: unknown command 'x'; try 'help'"
        );
        assert_eq!(
            ParseError::UnexpectedArgument {
                command: "mode",
                argument: "x".into(),
            }
            .to_string(),
            "error: 'mode' takes no argument 'x'; try 'help'"
        );
        assert_eq!(
            ParseError::MissingLayerValue.to_string(),
            "error: 'contracts --layer' requires a layer key; try 'help'"
        );
        assert_eq!(
            ParseError::UnknownLayer("x".into()).to_string(),
            "error: unknown layer 'x' for 'contracts --layer'; try 'help'"
        );
    }
}
