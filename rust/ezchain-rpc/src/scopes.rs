//! Scoped-key normalisation, ported from `ezchain_api.services.api_keys`.
//!
//! The Python original raised `ApiKeyError` with two stable codes. The Rust port returns a
//! typed error carrying the same codes; the RPC boundary maps it back to the Python
//! exception class. The tests below are the recorded cases from the original, including
//! the Unicode and duplicate-grant edge cases.

/// The reason a scope grant was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScopeError {
    /// No scope was supplied (`API_KEY_SCOPES_REQUIRED`).
    Required,
    /// At least one unknown scope was supplied (`API_KEY_SCOPE_UNKNOWN`).
    /// The names are sorted, as in the original message.
    Unknown(Vec<String>),
}

const ALLOWED: [&str; 3] = ["admin", "read", "trade"];

/// Normalise a scope grant: reject empty or unknown scopes, deduplicate and sort.
pub fn normalize_scopes(scopes: Option<&[String]>) -> Result<Vec<String>, ScopeError> {
    let scopes = scopes.unwrap_or(&[]);
    if scopes.is_empty() {
        return Err(ScopeError::Required);
    }
    let mut unknown: Vec<String> = scopes
        .iter()
        .filter(|scope| !ALLOWED.contains(&scope.as_str()))
        .cloned()
        .collect();
    if !unknown.is_empty() {
        unknown.sort();
        unknown.dedup();
        return Err(ScopeError::Unknown(unknown));
    }
    let mut accepted: Vec<String> = scopes.to_vec();
    accepted.sort();
    accepted.dedup();
    Ok(accepted)
}

#[cfg(test)]
mod tests {
    use super::{normalize_scopes, ScopeError};

    fn grant(values: &[&str]) -> Option<Vec<String>> {
        Some(values.iter().map(|value| value.to_string()).collect())
    }

    #[test]
    fn empty_and_missing_grants_are_refused() {
        assert_eq!(normalize_scopes(None), Err(ScopeError::Required));
        assert_eq!(normalize_scopes(Some(&[])), Err(ScopeError::Required));
    }

    #[test]
    fn grants_are_sorted_and_deduplicated() {
        assert_eq!(
            normalize_scopes(grant(&["trade", "read"]).as_deref()),
            Ok(vec!["read".into(), "trade".into()])
        );
        assert_eq!(
            normalize_scopes(grant(&["admin", "trade", "read", "read", "trade"]).as_deref()),
            Ok(vec!["admin".into(), "read".into(), "trade".into()])
        );
    }

    #[test]
    fn unknown_scopes_are_sorted_and_named() {
        assert_eq!(
            normalize_scopes(grant(&["read", "bogus", "admin"]).as_deref()),
            Err(ScopeError::Unknown(vec!["bogus".into()]))
        );
        assert_eq!(
            normalize_scopes(grant(&["hello", "./x/**/*.json"]).as_deref()),
            Err(ScopeError::Unknown(vec![
                "./x/**/*.json".into(),
                "hello".into()
            ]))
        );
    }

    #[test]
    fn case_and_unicode_are_unknown_not_normalised() {
        assert_eq!(
            normalize_scopes(grant(&["READ"]).as_deref()),
            Err(ScopeError::Unknown(vec!["READ".into()]))
        );
        assert_eq!(
            normalize_scopes(grant(&["ünïcödé", "日本語のテキスト", "a..b"]).as_deref()),
            Err(ScopeError::Unknown(vec![
                "a..b".into(),
                "ünïcödé".into(),
                "日本語のテキスト".into(),
            ]))
        );
    }
}
