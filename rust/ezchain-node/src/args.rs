//! Hand-written typed argument parser — the oracle for the command contract.
//!
//! No argument-parsing crate is used: there are two verbs and one flag. A refusal is a typed
//! error, never a silent fallback (CONTRACT I3).

use std::{fmt, path::PathBuf};

/// A parsed command. Nothing is read or bound until it is executed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Validate a config file without starting any transport.
    Check { config: PathBuf },
    /// Start both transports with one shutdown signal.
    Serve { config: PathBuf },
    /// Print usage.
    Help,
}

/// A typed command-line parse error.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    NoCommand,
    UnknownCommand(String),
    MissingConfig(&'static str),
    UnexpectedOption {
        command: &'static str,
        option: String,
    },
    UnexpectedArgument {
        command: &'static str,
        argument: String,
    },
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::NoCommand => write!(f, "error: no command supplied; try 'help'"),
            ParseError::UnknownCommand(verb) => {
                write!(f, "error: unknown command '{verb}'; try 'help'")
            }
            ParseError::MissingConfig(command) => {
                write!(
                    f,
                    "error: '{command}' requires '--config <file>'; try 'help'"
                )
            }
            ParseError::UnexpectedOption { command, option } => write!(
                f,
                "error: '{command}' does not accept option '{option}'; only '--config <file>'"
            ),
            ParseError::UnexpectedArgument { command, argument } => {
                write!(
                    f,
                    "error: '{command}' takes no argument '{argument}'; try 'help'"
                )
            }
        }
    }
}

/// Parse the argument vector *after* the program name into a typed [`Command`].
pub fn parse(args: &[String]) -> Result<Command, ParseError> {
    let verb = args.first().ok_or(ParseError::NoCommand)?;
    match verb.as_str() {
        "check" => config_command("check", args, |config| Command::Check { config }),
        "serve" => config_command("serve", args, |config| Command::Serve { config }),
        "help" | "-h" | "--help" => no_argument("help", args, Command::Help),
        other => Err(ParseError::UnknownCommand(other.to_string())),
    }
}

fn config_command(
    command: &'static str,
    args: &[String],
    build: impl FnOnce(PathBuf) -> Command,
) -> Result<Command, ParseError> {
    let flag = args.get(1).ok_or(ParseError::MissingConfig(command))?;
    if flag != "--config" {
        return Err(ParseError::UnexpectedOption {
            command,
            option: flag.clone(),
        });
    }
    let value = args.get(2).ok_or(ParseError::MissingConfig(command))?;
    if let Some(extra) = args.get(3) {
        return Err(ParseError::UnexpectedArgument {
            command,
            argument: extra.clone(),
        });
    }
    Ok(build(PathBuf::from(value)))
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

#[cfg(test)]
mod tests {
    use super::{parse, Command, ParseError};

    fn argv(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    #[test]
    fn check_and_serve_take_exactly_one_config_file() {
        assert_eq!(
            parse(&argv(&["check", "--config", "node.json"])),
            Ok(Command::Check {
                config: "node.json".into()
            })
        );
        assert_eq!(
            parse(&argv(&["serve", "--config", "node.json"])),
            Ok(Command::Serve {
                config: "node.json".into()
            })
        );
        assert_eq!(parse(&argv(&["help"])), Ok(Command::Help));
        assert_eq!(parse(&argv(&["--help"])), Ok(Command::Help));
    }

    #[test]
    fn malformed_command_lines_are_typed_refusals() {
        assert_eq!(parse(&[]), Err(ParseError::NoCommand));
        assert_eq!(
            parse(&argv(&["bogus"])),
            Err(ParseError::UnknownCommand("bogus".into()))
        );
        assert_eq!(
            parse(&argv(&["serve"])),
            Err(ParseError::MissingConfig("serve"))
        );
        assert_eq!(
            parse(&argv(&["check", "--config"])),
            Err(ParseError::MissingConfig("check"))
        );
        assert_eq!(
            parse(&argv(&["check", "--port", "1"])),
            Err(ParseError::UnexpectedOption {
                command: "check",
                option: "--port".into()
            })
        );
        assert_eq!(
            parse(&argv(&["serve", "--config", "a", "extra"])),
            Err(ParseError::UnexpectedArgument {
                command: "serve",
                argument: "extra".into()
            })
        );
        assert!(parse(&argv(&["serve"]))
            .unwrap_err()
            .to_string()
            .contains("--config"));
    }
}
