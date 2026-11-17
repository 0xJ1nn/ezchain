//! Binary entry point: parse, run, print, set the exit code. No logic lives here.
//!
//! Exit codes follow `tools/ops/cli.py`: 0 for success, 2 for a refusal or a bad command
//! line. A refusal is an error, never a silent pass.

#![forbid(unsafe_code)]

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match ezchain_cli::run(&args) {
        Ok(lines) => {
            for line in lines {
                println!("{line}");
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}
