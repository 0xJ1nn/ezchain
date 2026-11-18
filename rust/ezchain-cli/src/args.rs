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
    /// Report the full ADR-0050 language map as an aligned table.
    ContractsAll,
    /// Report the ADR-0052 ownership view, optionally one named section.
    Policy(Option<String>),
    /// List the required port gates from the documented policy.
    Gate,
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
    /// `policy --layer` was given without a value.
    MissingPolicyLayerValue,
    /// `policy --layer` was given a key that is not an ADR-0052 section.
    UnknownPolicyLayer(String),
    /// `refuse` was given no verb to demonstrate.
    MissingRefusalVerb,
    /// `refuse` was given a verb that is not a recognised refused operator verb.
    UnknownRefusalVerb(String),
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
            ParseError::MissingPolicyLayerValue => {
                write!(
                    f,
                    "error: 'policy --layer' requires a section key; try 'help'"
                )
            }
            ParseError::UnknownPolicyLayer(key) => {
                write!(
                    f,
                    "error: unknown section '{key}' for 'policy --layer'; try 'help'"
                )
            }
            ParseError::MissingRefusalVerb => {
                write!(f, "error: 'refuse' requires a verb; try 'help'")
            }
            ParseError::UnknownRefusalVerb(verb) => {
                write!(
                    f,
                    "error: '{verb}' is not a refused operator verb for 'refuse'; try 'help'"
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
        "policy" => parse_policy(args),
        "gate" => no_argument("gate", args, Command::Gate),
        "refuse" => parse_refuse(args),
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
            if args[1] == "--all" {
                Ok(Command::ContractsAll)
            } else if args[1] == "--layer" {
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

fn parse_policy(args: &[String]) -> Result<Command, ParseError> {
    match args.len() {
        1 => Ok(Command::Policy(None)),
        2 => {
            if args[1] == "--layer" {
                Err(ParseError::MissingPolicyLayerValue)
            } else {
                Err(ParseError::UnexpectedArgument {
                    command: "policy",
                    argument: args[1].clone(),
                })
            }
        }
        3 => {
            if args[1] != "--layer" {
                Err(ParseError::UnexpectedArgument {
                    command: "policy",
                    argument: args[1].clone(),
                })
            } else if !crate::commands::is_known_policy_layer(&args[2]) {
                Err(ParseError::UnknownPolicyLayer(args[2].clone()))
            } else {
                Ok(Command::Policy(Some(args[2].clone())))
            }
        }
        _ => Err(ParseError::UnexpectedArgument {
            command: "policy",
            argument: args.get(3).cloned().unwrap_or_else(|| args[1].clone()),
        }),
    }
}

fn parse_refuse(args: &[String]) -> Result<Command, ParseError> {
    let verb = args.get(1).ok_or(ParseError::MissingRefusalVerb)?;
    match refusal_for(verb.as_str()) {
        Some(refusal) => match args.get(2) {
            Some(extra) => Err(ParseError::UnexpectedArgument {
                command: "refuse",
                argument: extra.clone(),
            }),
            None => Ok(Command::Refused(refusal)),
        },
        None => Err(ParseError::UnknownRefusalVerb(verb.clone())),
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
        assert_eq!(
            parse(&argv(&["contracts", "--all"])),
            Ok(Command::ContractsAll)
        );
    }

    #[test]
    fn policy_verb_parses() {
        assert_eq!(parse(&argv(&["policy"])), Ok(Command::Policy(None)));
        assert_eq!(
            parse(&argv(&["policy", "--layer", "rust"])),
            Ok(Command::Policy(Some("rust".into())))
        );
        assert_eq!(
            parse(&argv(&["policy", "--layer", "integration"])),
            Ok(Command::Policy(Some("integration".into())))
        );
    }

    #[test]
    fn policy_error_branches() {
        assert_eq!(
            parse(&argv(&["policy", "--layer"])),
            Err(ParseError::MissingPolicyLayerValue)
        );
        assert_eq!(
            parse(&argv(&["policy", "rust"])),
            Err(ParseError::UnexpectedArgument {
                command: "policy",
                argument: "rust".into(),
            })
        );
        assert_eq!(
            parse(&argv(&["policy", "--layer", "nope"])),
            Err(ParseError::UnknownPolicyLayer("nope".into()))
        );
        assert_eq!(
            parse(&argv(&["policy", "--layer", "core"])),
            Err(ParseError::UnknownPolicyLayer("core".into()))
        );
        assert_eq!(
            parse(&argv(&["policy", "--layer", "rust", "extra"])),
            Err(ParseError::UnexpectedArgument {
                command: "policy",
                argument: "extra".into(),
            })
        );
    }

    #[test]
    fn gate_verb_parses() {
        assert_eq!(parse(&argv(&["gate"])), Ok(Command::Gate));
    }

    #[test]
    fn refuse_verb_returns_the_typed_refusal_for_every_category() {
        let cases = [
            ("order", RefusalCategory::Money),
            ("live", RefusalCategory::LiveMode),
            ("sign", RefusalCategory::Keys),
            ("quote", RefusalCategory::Venue),
            ("backup", RefusalCategory::Database),
        ];
        for (verb, category) in cases {
            assert_eq!(
                parse(&argv(&["refuse", verb])),
                Ok(Command::Refused(Refusal { verb, category })),
                "verb {verb}"
            );
        }
    }

    #[test]
    fn refuse_error_branches() {
        assert_eq!(
            parse(&argv(&["refuse"])),
            Err(ParseError::MissingRefusalVerb)
        );
        assert_eq!(
            parse(&argv(&["refuse", "bogus"])),
            Err(ParseError::UnknownRefusalVerb("bogus".into()))
        );
        // A read-only command is never a refused verb, even through `refuse`.
        assert_eq!(
            parse(&argv(&["refuse", "version"])),
            Err(ParseError::UnknownRefusalVerb("version".into()))
        );
        assert_eq!(
            parse(&argv(&["refuse", "order", "extra"])),
            Err(ParseError::UnexpectedArgument {
                command: "refuse",
                argument: "extra".into(),
            })
        );
    }

    #[test]
    fn unexpected_arguments_on_no_argument_commands_are_errors() {
        for verb in ["version", "mode", "help", "gate"] {
            assert_eq!(
                parse(&argv(&[verb, "extra"])),
                Err(ParseError::UnexpectedArgument {
                    command: verb,
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
        assert_eq!(
            ParseError::MissingPolicyLayerValue.to_string(),
            "error: 'policy --layer' requires a section key; try 'help'"
        );
        assert_eq!(
            ParseError::UnknownPolicyLayer("x".into()).to_string(),
            "error: unknown section 'x' for 'policy --layer'; try 'help'"
        );
        assert_eq!(
            ParseError::MissingRefusalVerb.to_string(),
            "error: 'refuse' requires a verb; try 'help'"
        );
        assert_eq!(
            ParseError::UnknownRefusalVerb("x".into()).to_string(),
            "error: 'x' is not a refused operator verb for 'refuse'; try 'help'"
        );
    }
}
