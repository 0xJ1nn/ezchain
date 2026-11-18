//! Pure `to_dict` serialisation spellings for money and market/quote records.
//!
//! This module reproduces the *shape and spelling* of the dicts returned by
//! `packages/core-py/src/ezchain_core/money.py::CostPreview.to_dict`,
//! `types.py::SourceRef.to_dict` and `types.py::Quote.to_dict`. It is the part
//! of the "JSON canonicalisation" blocker that is a pure function of already
//! validated values: no parsing, rounding, conversion, I/O or keys.
//!
//! Two spellings are proved here, both verbatim from the Python originals:
//!
//! * a money value is emitted as `str(Decimal)` — [`Decimal::str_repr`] already
//!   reproduces CPython's `str` including signed zeros and `0E-8` — and `None`
//!   becomes a JSON null;
//! * a timestamp is emitted as Python's `datetime.isoformat()` after the
//!   original normalises it to UTC: `YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`.
//!
//! The caller hands in the already UTC-normalised datetime components and the
//! exact decimal lexemes, exactly as the Python objects hold them. Rust never
//! reinterprets a value; it commits to the spelling it was given. The byte
//! string, where one is needed, is produced by `ezchain-jsoncanon` in the
//! parity gate (a dev-only dependency; this library itself has none).
//!
//! The Python implementation remains the oracle. Only the records listed in
//! [`README.md`](../README.md) are claimed as proven.

#![forbid(unsafe_code)]

use crate::Decimal;

/// A UTC timestamp as the components Python's `datetime.isoformat()` prints.
///
/// `SourceRef.to_dict` first normalises `observed_at` to UTC (`astimezone(UTC)`
/// for an aware datetime, `replace(tzinfo=UTC)` for a naive one), so the
/// emitted spelling always carries a `+00:00` offset. The oracle records the
/// normalised components; this type only reproduces the string spelling.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UtcDateTime {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    pub microsecond: u32,
}

impl UtcDateTime {
    /// Python `datetime.isoformat()` for a UTC-aware value: a six-digit
    /// fractional part only when microseconds are non-zero, and a `+00:00`
    /// offset.
    pub fn isoformat(&self) -> String {
        let mut out = format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        );
        if self.microsecond != 0 {
            out.push_str(&format!(".{:06}", self.microsecond));
        }
        out.push_str("+00:00");
        out
    }
}

/// A value a `to_dict` field map can hold. This is a dependency-free local
/// mirror so the library returns typed field maps without pulling in a JSON
/// crate; the parity gate converts it to `ezchain_jsoncanon::JsonValue` when a
/// canonical byte string is needed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DictValue {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Object(Vec<(&'static str, DictValue)>),
    Array(Vec<DictValue>),
}

/// The dict returned by `types.SourceRef.to_dict()`: `name`, `url` (or null)
/// and the UTC `observed_at` ISO spelling.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceRefDict {
    pub name: String,
    pub url: Option<String>,
    pub observed_at: String,
}

impl SourceRefDict {
    /// The Python dict as an ordered field map. Key order is irrelevant to the
    /// canonical writer, which sorts keys as `sort_keys=True` does.
    pub fn fields(&self) -> Vec<(&'static str, DictValue)> {
        vec![
            ("name", DictValue::Str(self.name.clone())),
            (
                "url",
                match &self.url {
                    Some(url) => DictValue::Str(url.clone()),
                    None => DictValue::Null,
                },
            ),
            ("observed_at", DictValue::Str(self.observed_at.clone())),
        ]
    }
}

/// The Python `SourceRef` value, carrying exactly the fields `to_dict` reads.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceRef {
    pub name: String,
    pub url: Option<String>,
    /// Already normalised to UTC by the caller/oracle; only the `isoformat`
    /// spelling is reproduced here.
    pub observed_at: UtcDateTime,
}

impl SourceRef {
    pub fn to_dict(&self) -> SourceRefDict {
        SourceRefDict {
            name: self.name.clone(),
            url: self.url.clone(),
            observed_at: self.observed_at.isoformat(),
        }
    }
}

/// The dict returned by `money.CostPreview.to_dict()`. Every money field is
/// the `str(Decimal)` spelling (or null), including the echoed
/// `network_fee_usd` / `price_impact_pct` fields and the nested `source` dict.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CostPreviewDict {
    pub platform_fee_usd: String,
    pub venue_fee_usd: String,
    pub network_fee_usd: Option<String>,
    pub price_impact_pct: Option<String>,
    pub slippage_bps: Option<i64>,
    pub worst_case_out: Option<String>,
    pub fill_fee_usd: String,
    pub source: SourceRefDict,
}

impl CostPreviewDict {
    pub fn fields(&self) -> Vec<(&'static str, DictValue)> {
        vec![
            (
                "platform_fee_usd",
                DictValue::Str(self.platform_fee_usd.clone()),
            ),
            ("venue_fee_usd", DictValue::Str(self.venue_fee_usd.clone())),
            (
                "network_fee_usd",
                match &self.network_fee_usd {
                    Some(value) => DictValue::Str(value.clone()),
                    None => DictValue::Null,
                },
            ),
            (
                "price_impact_pct",
                match &self.price_impact_pct {
                    Some(value) => DictValue::Str(value.clone()),
                    None => DictValue::Null,
                },
            ),
            (
                "slippage_bps",
                match self.slippage_bps {
                    Some(value) => DictValue::Int(value),
                    None => DictValue::Null,
                },
            ),
            (
                "worst_case_out",
                match &self.worst_case_out {
                    Some(value) => DictValue::Str(value.clone()),
                    None => DictValue::Null,
                },
            ),
            ("fill_fee_usd", DictValue::Str(self.fill_fee_usd.clone())),
            ("source", DictValue::Object(self.source.fields())),
        ]
    }
}

/// The Python `money.CostPreview` value, including the pure echo fields the
/// numeric prototype did not carry. No arithmetic happens here: every field is
/// already a validated decimal or a null, exactly as in the Python object.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CostPreviewRecord {
    pub platform_fee_usd: Decimal,
    pub venue_fee_usd: Decimal,
    pub network_fee_usd: Option<Decimal>,
    pub price_impact_pct: Option<Decimal>,
    pub slippage_bps: Option<i64>,
    pub worst_case_out: Option<Decimal>,
    pub fill_fee_usd: Decimal,
    pub source: SourceRef,
}

fn dec_str(value: &Decimal) -> String {
    value.str_repr()
}

fn opt_dec_str(value: &Option<Decimal>) -> Option<String> {
    value.as_ref().map(dec_str)
}

impl CostPreviewRecord {
    /// The Python `CostPreview.to_dict()` spelling, verbatim.
    pub fn to_dict(&self) -> CostPreviewDict {
        CostPreviewDict {
            platform_fee_usd: dec_str(&self.platform_fee_usd),
            venue_fee_usd: dec_str(&self.venue_fee_usd),
            network_fee_usd: opt_dec_str(&self.network_fee_usd),
            price_impact_pct: opt_dec_str(&self.price_impact_pct),
            slippage_bps: self.slippage_bps,
            worst_case_out: opt_dec_str(&self.worst_case_out),
            fill_fee_usd: dec_str(&self.fill_fee_usd),
            source: self.source.to_dict(),
        }
    }
}

/// The dict returned by `types.Quote.to_dict()`: a market/quote record whose
/// money fields use the same `str(Decimal)` / null spellings and whose `source`
/// is the nested `SourceRef` dict. `raw` is deliberately absent, exactly as in
/// the Python method.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QuoteDict {
    pub chain: String,
    pub token_in: String,
    pub token_out: String,
    pub side: String,
    pub amount_in: String,
    pub amount_out: String,
    pub price: String,
    pub price_impact_pct: Option<String>,
    pub fee_usd: String,
    pub kind: String,
    pub route: Option<String>,
    pub source: SourceRefDict,
}

impl QuoteDict {
    pub fn fields(&self) -> Vec<(&'static str, DictValue)> {
        vec![
            ("chain", DictValue::Str(self.chain.clone())),
            ("token_in", DictValue::Str(self.token_in.clone())),
            ("token_out", DictValue::Str(self.token_out.clone())),
            ("side", DictValue::Str(self.side.clone())),
            ("amount_in", DictValue::Str(self.amount_in.clone())),
            ("amount_out", DictValue::Str(self.amount_out.clone())),
            ("price", DictValue::Str(self.price.clone())),
            (
                "price_impact_pct",
                match &self.price_impact_pct {
                    Some(value) => DictValue::Str(value.clone()),
                    None => DictValue::Null,
                },
            ),
            ("fee_usd", DictValue::Str(self.fee_usd.clone())),
            ("kind", DictValue::Str(self.kind.clone())),
            (
                "route",
                match &self.route {
                    Some(value) => DictValue::Str(value.clone()),
                    None => DictValue::Null,
                },
            ),
            ("source", DictValue::Object(self.source.fields())),
        ]
    }
}

/// The Python `types.Quote` value (the fields `to_dict` reads).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QuoteRecord {
    pub chain: String,
    pub token_in: String,
    pub token_out: String,
    pub side: String,
    pub amount_in: Decimal,
    pub amount_out: Decimal,
    pub price: Decimal,
    pub price_impact_pct: Option<Decimal>,
    pub fee_usd: Decimal,
    pub kind: String,
    pub route: Option<String>,
    pub source: SourceRef,
}

impl QuoteRecord {
    pub fn to_dict(&self) -> QuoteDict {
        QuoteDict {
            chain: self.chain.clone(),
            token_in: self.token_in.clone(),
            token_out: self.token_out.clone(),
            side: self.side.clone(),
            amount_in: dec_str(&self.amount_in),
            amount_out: dec_str(&self.amount_out),
            price: dec_str(&self.price),
            price_impact_pct: opt_dec_str(&self.price_impact_pct),
            fee_usd: dec_str(&self.fee_usd),
            kind: self.kind.clone(),
            route: self.route.clone(),
            source: self.source.to_dict(),
        }
    }
}
