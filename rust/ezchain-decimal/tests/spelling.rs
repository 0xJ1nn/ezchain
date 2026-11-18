//! Focused parity gate for decimal *spellings* recorded by `spelling_oracle.py`
//! against the real Python `decimal.Decimal`.
//!
//! The main parity fixture uses canonical literals only. This gate adds the
//! alternatives the review found missing: underscores, Unicode Nd digits from
//! the four covered blocks, signs, exponents, whitespace and non-finite words.
//! Expected tokens include exact `str(Decimal)` output and exact refusal codes.
//! If a case fails, fix the parser and regenerate from the oracle — never edit
//! the fixture to go green.

use ezchain_decimal::{Decimal, ParseError};

const FIXTURE: &str = include_str!("spelling_fixture.txt");
/// Pinned so a truncated or stale fixture cannot silently reduce coverage.
const EXPECTED_CASES: usize = 127;

/// Decode the hex-encoded UTF-8 spelling field without a dependency.
fn decode_hex(field: &str) -> String {
    let bytes: Vec<u8> = field
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| {
            let hi = char::from(pair[0]).to_digit(16).expect("hex digit");
            let lo = char::from(pair[1]).to_digit(16).expect("hex digit");
            (hi * 16 + lo) as u8
        })
        .collect();
    String::from_utf8(bytes).expect("fixture spelling is valid UTF-8")
}

/// The exact outcome token recorded by the Python oracle.
fn outcome(spelling: &str) -> String {
    match Decimal::parse(spelling) {
        Ok(value) => format!("V:{}~{}", value.canonical(), value.str_repr()),
        Err(ParseError::NonFinite) => "E:NonFinite".to_owned(),
        Err(ParseError::Syntax) => "E:ConversionSyntax".to_owned(),
        // CPython raises InvalidOperation (not ConversionSyntax) when an
        // exponent construction exceeds MAX_EMAX.
        Err(ParseError::ExpOutOfRange) => "E:InvalidOperation".to_owned(),
    }
}

#[test]
fn python_oracle_spellings_match() {
    let mut passed = 0usize;
    let mut failures = Vec::new();
    let mut total = 0usize;

    for (index, line) in FIXTURE.lines().enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        assert!(fields.len() == 3, "malformed fixture line {}", index + 1);
        let spelling = decode_hex(fields[0]);
        let label = fields[1];
        let expected = fields[2];
        let actual = outcome(&spelling);
        total += 1;
        if actual == expected {
            passed += 1;
        } else {
            failures.push(format!(
                "line {} [{label}]: expected {expected}, got {actual}",
                index + 1
            ));
        }
    }

    assert_eq!(total, EXPECTED_CASES, "spelling fixture case count drifted");
    eprintln!("spelling parity: {passed}/{total} cases match the Python oracle");
    if !failures.is_empty() {
        for failure in failures.iter().take(20) {
            eprintln!("  MISMATCH {failure}");
        }
        panic!("{} spelling case(s) failed", failures.len());
    }
}
