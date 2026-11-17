//! Source-freshness vocabulary, ported from `ezchain_core.freshness`.
//!
//! The Python original is the behavioural oracle. `stale_reason` is a pure classifier:
//! a quote refusal names the quote reason, every other source class names the snapshot
//! reason. The tests below are the recorded cases from the original module.

/// The stable reason code a stale read of `source_class` reports.
pub fn stale_reason(source_class: &str) -> &'static str {
    if source_class == "quote" {
        "QUOTE_STALE"
    } else {
        "SNAPSHOT_STALE"
    }
}

#[cfg(test)]
mod tests {
    use super::stale_reason;

    #[test]
    fn quote_uses_the_quote_reason() {
        assert_eq!(stale_reason("quote"), "QUOTE_STALE");
    }

    #[test]
    fn every_other_class_uses_the_snapshot_reason() {
        for class in ["mark", "candle", "feed", "", "unknown-class"] {
            assert_eq!(stale_reason(class), "SNAPSHOT_STALE", "class {class}");
        }
    }
}
