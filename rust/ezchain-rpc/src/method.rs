//! HTTP method normalisation, ported from `ezchain_api.observability`.
//!
//! The Python original is two lines: uppercase the whole string with Unicode's full
//! casing mapping, then test membership in a fixed seven-method set, returning `OTHER`
//! otherwise. The Rust port keeps that order and that vocabulary exactly. In particular it
//! uppercases *before* the membership test, so an input such as `poſt` (long s) or `optıons`
//! (dotless i) uppercases to `POST`/`OPTIONS` and is accepted — this is Python behaviour,
//! not a normalisation we invented. The recorded cases below come from running the real
//! Python function in the worktree source; each expected value is that function's output.

/// The single bounded label for a method outside the safe set.
pub const OTHER: &str = "OTHER";

/// The safe method set, mirroring `SAFE_METHODS` in the Python original.
const SAFE_METHODS: [&str; 7] = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"];

/// Normalise an HTTP method to a bounded metric label.
///
/// Returns one of the seven safe methods (always in canonical uppercase) or `OTHER`.
/// An empty string, whitespace, trailing characters and unknown verbs all fold to `OTHER`,
/// so the label set stays bounded no matter what a caller sends.
pub fn normalize_method(method: &str) -> &'static str {
    let upper = method.to_uppercase();
    SAFE_METHODS
        .iter()
        .copied()
        .find(|safe| *safe == upper)
        .unwrap_or(OTHER)
}

#[cfg(test)]
mod tests {
    use super::{normalize_method, OTHER};

    // Recorded Python cases: (input, normalize_method(input)).
    // Source: ezchain_api.observability.normalize_method, recorded with Python 3.11.
    fn assert_cases(cases: &[(&str, &str)]) {
        for (input, expected) in cases {
            assert_eq!(
                normalize_method(input),
                *expected,
                "recorded case {input:?}"
            );
        }
    }

    #[test]
    fn safe_methods_and_case_variants() {
        // Canonical methods and every lowercase / title / mixed-case spelling.
        assert_cases(&[
            ("GET", "GET"),
            ("POST", "POST"),
            ("PATCH", "PATCH"),
            ("DELETE", "DELETE"),
            ("PUT", "PUT"),
            ("HEAD", "HEAD"),
            ("OPTIONS", "OPTIONS"),
            ("get", "GET"),
            ("post", "POST"),
            ("patch", "PATCH"),
            ("delete", "DELETE"),
            ("put", "PUT"),
            ("head", "HEAD"),
            ("options", "OPTIONS"),
            ("Get", "GET"),
            ("Post", "POST"),
            ("Patch", "PATCH"),
            ("Delete", "DELETE"),
            ("Put", "PUT"),
            ("Head", "HEAD"),
            ("Options", "OPTIONS"),
            ("gEt", "GET"),
            ("pOsT", "POST"),
            ("pAtCh", "PATCH"),
            ("dElEtE", "DELETE"),
            ("pUt", "PUT"),
            ("hEaD", "HEAD"),
            ("oPtIoNs", "OPTIONS"),
        ]);
    }

    #[test]
    fn unknown_and_near_miss_methods_fold_to_other() {
        // The error/other branch: empty, whitespace, unknown verbs, and near misses that
        // differ from a safe method by one character. Nothing here is trimmed or guessed.
        assert_cases(&[
            ("", OTHER),
            (" ", OTHER),
            ("BREW", OTHER),
            ("brew", OTHER),
            ("PROPFIND", OTHER),
            ("TRACE", OTHER),
            ("CONNECT", OTHER),
            ("GET ", OTHER),
            (" GET", OTHER),
            ("GET\t", OTHER),
            ("get\n", OTHER),
            ("get/post", OTHER),
            ("g3t", OTHER),
            ("123", OTHER),
            ("get;", OTHER),
            ("GET\0", OTHER),
            ("GET\u{200b}", OTHER),
            ("MKCOL", OTHER),
            ("COPY", OTHER),
            ("MOVE", OTHER),
            ("LOCK", OTHER),
            ("UNLOCK", OTHER),
            ("SEARCH", OTHER),
        ]);
    }

    #[test]
    fn unicode_uppercase_matches_python() {
        // Full Unicode casing, recorded from the Python oracle. Four of these uppercase
        // INTO a safe method (poſt, optionſ, optıons) and must be accepted, exactly as in
        // Python; the rest must remain OTHER. Rust's str::to_uppercase and Python's
        // str.upper agree on every case here.
        assert_cases(&[
            ("ß", OTHER),
            ("ı", OTHER),
            ("i", OTHER),
            ("İ", OTHER),
            ("ﬁ", OTHER),
            ("K", OTHER),
            ("ｇｅｔ", OTHER),
            ("é", OTHER),
            ("gét", OTHER),
            ("日本語", OTHER),
            ("😀", OTHER),
            ("poſt", "POST"),
            ("optionſ", "OPTIONS"),
            ("optıons", "OPTIONS"),
            ("gęt", OTHER),
            ("gÉt", OTHER),
            ("GEſ", OTHER),
            ("poſt ", OTHER),
            ("poſt\n", OTHER),
        ]);
    }
}
