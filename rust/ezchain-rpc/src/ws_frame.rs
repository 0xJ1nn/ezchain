//! Pure RFC 6455 frame codec for the node-facing WebSocket transport.
//!
//! # What this is
//!
//! Frame bytes in, frame bytes out. This is a **pure byte codec**: it never opens a socket,
//! never performs the HTTP `Upgrade` handshake, never runs an async runtime, and never
//! interprets a message. Fragment reassembly, ping/pong scheduling and the close handshake
//! belong to a connection layer above it. A future transport owns the connection and calls
//! [`encode`] / [`decode`] on whole buffers.
//!
//! This is the **node transport primitive** under ADR-0050/0052: Rust owns node-facing
//! networking and WebSockets. The product-facing WebSocket gateway stays in Python in
//! `apps/api` (ADR-0052); do not move that gateway into this crate. No Python original
//! exists for this primitive, so this written contract — and the cases recorded in the tests
//! below — is the oracle.
//!
//! # Frame layout (RFC 6455 §5.2)
//!
//! ```text
//! 0                   1                   2                   3
//! 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
//! +-+-+-+-+-------+-+-------------+-------------------------------+
//! |F|R|R|R| opcode|M| Payload len |    Extended payload length    |
//! |I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
//! |N|V|V|V|       |S|             |   (if payload len==126/127)   |
//! | |1|2|3|       |K|             |                               |
//! +-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
//! |     Extended payload length continued, if payload len == 127  |
//! + - - - - - - - - - - - - - - - +-------------------------------+
//! |                               | Masking-key, if MASK set to 1  |
//! +-------------------------------+-------------------------------+
//! | Masking-key (continued)       |          Payload Data         |
//! +-------------------------------+ - - - - - - - - - - - - - - - +
//! | Payload Data continued ...                                    |
//! +---------------------------------------------------------------+
//! ```
//!
//! # Direction and masking
//!
//! RFC 6455 requires a client to mask every frame it sends and forbids a server from
//! masking any frame it sends. [`Direction`] makes that a typed argument on both sides:
//! [`encode`] refuses a client-to-server frame without a [`MaskKey`] and refuses a
//! server-to-client frame with one; [`decode`] refuses a frame whose MASK bit disagrees
//! with the expected direction. A [`MaskKey`] is a four-byte wire nonce, **not** a secret
//! and not key material in the custody sense.
//!
//! # Length forms and limits
//!
//! Lengths 0..=125 use the 7-bit field, 126..=65535 use the 16-bit form (marker `126`),
//! and larger lengths use the 64-bit form (marker `127`). The 64-bit form's high bit must
//! be zero, so the largest representable payload is [`MAX_WIRE_LEN`]. Encoders always use
//! the shortest form and decoders reject a non-minimal one.
//!
//! [`Limits`] is supplied by the caller (CONTRACT I7): a decoder checks the declared length
//! against it **before** reading a masking key or allocating a payload, so a lying header
//! cannot force a large allocation. An encoder refuses to emit a frame the peer would reject
//! under the same limit. As with [`crate::framing`], [`decode`] is strict: its input must be
//! exactly one frame and surplus bytes are a [`DecodeError::TrailingBytes`] refusal.

/// FIN bit in the first header byte: this frame is the last of its message.
pub const FIN: u8 = 0x80;
/// RSV1 bit in the first header byte. No extension is negotiated, so it must be zero.
pub const RSV1: u8 = 0x40;
/// RSV2 bit in the first header byte. No extension is negotiated, so it must be zero.
pub const RSV2: u8 = 0x20;
/// RSV3 bit in the first header byte. No extension is negotiated, so it must be zero.
pub const RSV3: u8 = 0x10;
/// All three reserved bits, for validation in one operation.
pub const RSV_MASK: u8 = RSV1 | RSV2 | RSV3;
/// Mask selecting the four opcode bits of the first header byte.
pub const OPCODE_MASK: u8 = 0x0F;
/// MASK bit in the second header byte: a four-byte masking key follows the length.
pub const MASK: u8 = 0x80;
/// Mask selecting the seven payload-length bits of the second header byte.
pub const LEN_MASK: u8 = 0x7F;
/// Second-byte value introducing a two-byte extended length.
pub const LEN_16: u8 = 126;
/// Second-byte value introducing an eight-byte extended length.
pub const LEN_64: u8 = 127;
/// Largest payload a control frame may carry (RFC 6455 §5.5).
pub const MAX_CONTROL_PAYLOAD: u64 = 125;
/// Bytes in a masking key (RFC 6455 §5.3).
pub const MASK_KEY_LEN: usize = 4;
/// Largest length the 64-bit wire form may express: its high bit must be zero.
pub const MAX_WIRE_LEN: u64 = 0x7FFF_FFFF_FFFF_FFFF;

/// The declared RFC 6455 opcodes.
///
/// The byte values are fixed wire constants, not indices into an array. Reserved opcodes
/// (`0x3..=0x7`, `0xB..=0xF`) are deliberately absent: they are a typed decode refusal,
/// never an accidental member of this enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpCode {
    /// `0x0`: a continuation of a previously started fragmented message.
    Continuation,
    /// `0x1`: a UTF-8 text message.
    Text,
    /// `0x2`: an opaque binary message.
    Binary,
    /// `0x8`: close handshake.
    Close,
    /// `0x9`: ping.
    Ping,
    /// `0xA`: pong.
    Pong,
}

impl OpCode {
    pub const CONTINUATION_BYTE: u8 = 0x0;
    pub const TEXT_BYTE: u8 = 0x1;
    pub const BINARY_BYTE: u8 = 0x2;
    pub const CLOSE_BYTE: u8 = 0x8;
    pub const PING_BYTE: u8 = 0x9;
    pub const PONG_BYTE: u8 = 0xA;

    /// Parse a declared opcode byte, or `None` for a reserved value.
    pub fn from_byte(byte: u8) -> Option<Self> {
        match byte {
            Self::CONTINUATION_BYTE => Some(Self::Continuation),
            Self::TEXT_BYTE => Some(Self::Text),
            Self::BINARY_BYTE => Some(Self::Binary),
            Self::CLOSE_BYTE => Some(Self::Close),
            Self::PING_BYTE => Some(Self::Ping),
            Self::PONG_BYTE => Some(Self::Pong),
            _ => None,
        }
    }

    /// The fixed wire byte for this opcode.
    pub fn to_byte(self) -> u8 {
        match self {
            Self::Continuation => Self::CONTINUATION_BYTE,
            Self::Text => Self::TEXT_BYTE,
            Self::Binary => Self::BINARY_BYTE,
            Self::Close => Self::CLOSE_BYTE,
            Self::Ping => Self::PING_BYTE,
            Self::Pong => Self::PONG_BYTE,
        }
    }

    /// Control frames (the high bit of the opcode is set) may not be fragmented and may
    /// carry at most [`MAX_CONTROL_PAYLOAD`] bytes.
    pub fn is_control(self) -> bool {
        matches!(self, Self::Close | Self::Ping | Self::Pong)
    }
}

/// Which way a frame travels. RFC 6455 fixes the masking rule for each direction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    /// Client → server. Frames must be masked.
    ToServer,
    /// Server → client. Frames must be unmasked.
    ToClient,
}

/// A four-byte RFC 6455 masking key. This is a protocol nonce, not a secret.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MaskKey(pub [u8; MASK_KEY_LEN]);

/// Caller-supplied payload size policy (CONTRACT I7). Kept caller-side so one codec can
/// serve several connections with different budgets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// Largest accepted payload in bytes. The effective cap is
    /// `min(max_payload_bytes, MAX_WIRE_LEN)`.
    pub max_payload_bytes: u64,
}

/// One decoded (or encodable) frame.
///
/// The MASK bit is not a field: it is fixed by [`Direction`], which [`encode`] and
/// [`decode`] both enforce. A close frame's payload is opaque here; interpreting its
/// optional status code is the message layer's job.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    /// FIN: this frame is the last fragment of its message. Control frames always set it.
    pub fin: bool,
    pub opcode: OpCode,
    pub payload: Vec<u8>,
}

impl Frame {
    /// A whole (unfragmented) message frame: FIN is set.
    pub fn new(opcode: OpCode, payload: Vec<u8>) -> Self {
        Self {
            fin: true,
            opcode,
            payload,
        }
    }

    /// A fragment of a message: FIN is clear. Not valid for control opcodes.
    pub fn fragment(opcode: OpCode, payload: Vec<u8>) -> Self {
        Self {
            fin: false,
            opcode,
            payload,
        }
    }
}

/// Why a frame could not be encoded. Checked before any bytes are produced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EncodeError {
    /// A control opcode was given with FIN clear. Control frames cannot be fragmented.
    FragmentedControl { code: u8 },
    /// A control frame carried more than [`MAX_CONTROL_PAYLOAD`] bytes.
    ControlPayloadTooLarge { length: usize },
    /// The payload exceeds the effective caller cap `min(limit, MAX_WIRE_LEN)`.
    Oversize { length: usize, max: u64 },
    /// A client-to-server frame needs a masking key; none was supplied.
    MissingMaskKey,
    /// A server-to-client frame must be unmasked; a masking key was supplied.
    UnexpectedMaskKey,
}

/// Why a buffer could not be decoded.
///
/// Variants are listed in the exact order [`decode`] checks them, so a buffer with several
/// faults always reports the same first reason:
///
/// 1. [`DecodeError::TruncatedHeader`] — fewer than two header bytes.
/// 2. [`DecodeError::ReservedBitsSet`] — RSV1/2/3 nonzero (no extension negotiated).
/// 3. [`DecodeError::ReservedOpcode`] — opcode outside the declared set.
/// 4. [`DecodeError::FragmentedControl`] — control opcode with FIN clear.
/// 5. [`DecodeError::WrongMaskBit`] — MASK bit disagrees with the direction.
/// 6. [`DecodeError::TruncatedExtendedLength`] — 16/64-bit length bytes missing.
/// 7. [`DecodeError::Invalid64BitLength`] — 64-bit length had its high bit set.
/// 8. [`DecodeError::NonMinimalLength`] — a short length used a longer form.
/// 9. [`DecodeError::ControlPayloadTooLarge`] — control payload above 125 bytes.
/// 10. [`DecodeError::Oversize`] — declared length above the caller's limit.
/// 11. [`DecodeError::TruncatedMaskKey`] — fewer than four masking-key bytes.
/// 12. [`DecodeError::TruncatedPayload`] — fewer payload bytes than declared.
/// 13. [`DecodeError::TrailingBytes`] — more bytes than the frame declares.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeError {
    /// Fewer than two bytes were supplied; `got` is the total byte count.
    TruncatedHeader { got: usize },
    /// One or more RSV bits were set; `bits` is their raw mask.
    ReservedBitsSet { bits: u8 },
    /// A reserved opcode byte was supplied.
    ReservedOpcode { code: u8 },
    /// A control opcode arrived with FIN clear.
    FragmentedControl { code: u8 },
    /// The MASK bit did not match the expected direction.
    WrongMaskBit { direction: Direction, masked: bool },
    /// The extended-length bytes were missing. `need` is 2 or 8; `got` is what remained.
    TruncatedExtendedLength { form: u8, need: usize, got: usize },
    /// The 64-bit length had its high bit set, which RFC 6455 forbids.
    Invalid64BitLength { raw: u64 },
    /// A length that fits a shorter form was encoded with a longer one.
    NonMinimalLength { form: u8, length: u64 },
    /// A control frame declared more than 125 payload bytes.
    ControlPayloadTooLarge { length: u64 },
    /// The declared length exceeds the caller's limit. Checked before reading the mask key
    /// or payload, so a lying header cannot make the decoder allocate a large buffer.
    Oversize { length: u64, max: u64 },
    /// A masked frame carried fewer than four masking-key bytes; `got` is what remained.
    TruncatedMaskKey { got: usize },
    /// The header declared more payload bytes than the buffer carries.
    TruncatedPayload { declared: u64, available: usize },
    /// The buffer carries more bytes than the header declares. The surplus is never ignored.
    TrailingBytes { declared: u64, available: usize },
}

fn effective_max(limits: Limits) -> u64 {
    limits.max_payload_bytes.min(MAX_WIRE_LEN)
}

/// XOR a payload with a masking key. XOR is its own inverse, so this both masks a
/// client-to-server payload on encode and unmasks it on decode.
fn mask(payload: &[u8], key: &MaskKey) -> Vec<u8> {
    payload
        .iter()
        .enumerate()
        .map(|(i, &byte)| byte ^ key.0[i % MASK_KEY_LEN])
        .collect()
}

/// Encode one frame into a fresh buffer.
///
/// `direction` fixes the MASK bit; `mask_key` must be `Some` for [`Direction::ToServer`]
/// and `None` for [`Direction::ToClient`]. Every refusal happens before any bytes are
/// produced. The shortest length form is always used.
pub fn encode(
    frame: &Frame,
    direction: Direction,
    mask_key: Option<MaskKey>,
    limits: Limits,
) -> Result<Vec<u8>, EncodeError> {
    let length = frame.payload.len();

    if direction == Direction::ToServer && mask_key.is_none() {
        return Err(EncodeError::MissingMaskKey);
    }
    if direction == Direction::ToClient && mask_key.is_some() {
        return Err(EncodeError::UnexpectedMaskKey);
    }

    if frame.opcode.is_control() {
        if !frame.fin {
            return Err(EncodeError::FragmentedControl {
                code: frame.opcode.to_byte(),
            });
        }
        if length > MAX_CONTROL_PAYLOAD as usize {
            return Err(EncodeError::ControlPayloadTooLarge { length });
        }
    }

    let max = effective_max(limits);
    if (length as u64) > max {
        return Err(EncodeError::Oversize { length, max });
    }

    let len = length as u64;
    let masked = direction == Direction::ToServer;
    let header_len = if len <= MAX_CONTROL_PAYLOAD {
        2
    } else if len <= u16::MAX as u64 {
        4
    } else {
        10
    };
    let key_len = if masked { MASK_KEY_LEN } else { 0 };
    let mut out = Vec::with_capacity(header_len + key_len + length);

    let mut first = frame.opcode.to_byte();
    if frame.fin {
        first |= FIN;
    }
    // RSV1..3 stay clear: this primitive negotiates no extensions.
    out.push(first);

    let mut second = 0u8;
    if masked {
        second |= MASK;
    }
    if len <= MAX_CONTROL_PAYLOAD {
        second |= len as u8;
    } else if len <= u16::MAX as u64 {
        second |= LEN_16;
    } else {
        second |= LEN_64;
    }
    out.push(second);

    if len > u16::MAX as u64 {
        out.extend_from_slice(&len.to_be_bytes());
    } else if len > MAX_CONTROL_PAYLOAD {
        out.extend_from_slice(&(len as u16).to_be_bytes());
    }

    if let Some(key) = mask_key {
        out.extend_from_slice(&key.0);
        out.extend_from_slice(&mask(&frame.payload, &key));
    } else {
        out.extend_from_slice(&frame.payload);
    }
    Ok(out)
}

/// Decode exactly one frame from `input`, or return the first typed [`DecodeError`].
///
/// Checks run in the order documented on [`DecodeError`]. On success every byte of `input`
/// belongs to the frame: there is no partial consumption and no leftover.
pub fn decode(input: &[u8], direction: Direction, limits: Limits) -> Result<Frame, DecodeError> {
    // 1. Fixed header.
    if input.len() < 2 {
        return Err(DecodeError::TruncatedHeader { got: input.len() });
    }
    let first = input[0];
    let second = input[1];

    // 2. Reserved bits: no extension is negotiated by this primitive.
    let rsv = first & RSV_MASK;
    if rsv != 0 {
        return Err(DecodeError::ReservedBitsSet { bits: rsv });
    }

    // 3. Opcode.
    let code = first & OPCODE_MASK;
    let opcode = OpCode::from_byte(code).ok_or(DecodeError::ReservedOpcode { code })?;

    // 4. FIN on control frames.
    let fin = first & FIN != 0;
    if opcode.is_control() && !fin {
        return Err(DecodeError::FragmentedControl { code });
    }

    // 5. MASK bit against the expected direction.
    let masked = second & MASK != 0;
    let expected_masked = direction == Direction::ToServer;
    if masked != expected_masked {
        return Err(DecodeError::WrongMaskBit { direction, masked });
    }

    // 6. Extended length bytes.
    let len7 = second & LEN_MASK;
    let mut cursor = 2usize;
    let length: u64 = match len7 {
        LEN_16 => {
            let available = input.len() - cursor;
            if available < 2 {
                return Err(DecodeError::TruncatedExtendedLength {
                    form: LEN_16,
                    need: 2,
                    got: available,
                });
            }
            let length = u64::from(u16::from_be_bytes([input[cursor], input[cursor + 1]]));
            cursor += 2;
            if length <= MAX_CONTROL_PAYLOAD {
                return Err(DecodeError::NonMinimalLength {
                    form: LEN_16,
                    length,
                });
            }
            length
        }
        LEN_64 => {
            let available = input.len() - cursor;
            if available < 8 {
                return Err(DecodeError::TruncatedExtendedLength {
                    form: LEN_64,
                    need: 8,
                    got: available,
                });
            }
            let raw = u64::from_be_bytes([
                input[cursor],
                input[cursor + 1],
                input[cursor + 2],
                input[cursor + 3],
                input[cursor + 4],
                input[cursor + 5],
                input[cursor + 6],
                input[cursor + 7],
            ]);
            cursor += 8;
            if raw > MAX_WIRE_LEN {
                return Err(DecodeError::Invalid64BitLength { raw });
            }
            if raw <= u16::MAX as u64 {
                return Err(DecodeError::NonMinimalLength {
                    form: LEN_64,
                    length: raw,
                });
            }
            raw
        }
        n => u64::from(n),
    };

    // 7. Control frame payload cap.
    if opcode.is_control() && length > MAX_CONTROL_PAYLOAD {
        return Err(DecodeError::ControlPayloadTooLarge { length });
    }

    // 8. Caller limit, before reading a mask key or allocating a payload.
    let max = effective_max(limits);
    if length > max {
        return Err(DecodeError::Oversize { length, max });
    }

    // 9. Masking key.
    let key = if masked {
        let available = input.len() - cursor;
        if available < MASK_KEY_LEN {
            return Err(DecodeError::TruncatedMaskKey { got: available });
        }
        let key = MaskKey([
            input[cursor],
            input[cursor + 1],
            input[cursor + 2],
            input[cursor + 3],
        ]);
        cursor += MASK_KEY_LEN;
        Some(key)
    } else {
        None
    };

    // 10/11. Payload must match the declared length exactly.
    let available = input.len() - cursor;
    if (available as u64) < length {
        return Err(DecodeError::TruncatedPayload {
            declared: length,
            available,
        });
    }
    if (available as u64) > length {
        return Err(DecodeError::TrailingBytes {
            declared: length,
            available,
        });
    }

    let payload = match key {
        Some(key) => mask(&input[cursor..], &key),
        None => input[cursor..].to_vec(),
    };
    Ok(Frame {
        fin,
        opcode,
        payload,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIMIT: Limits = Limits {
        max_payload_bytes: 1 << 20,
    };
    const KEY: MaskKey = MaskKey([0x0a, 0x0b, 0x0c, 0x0d]);

    fn wire(first: u8, second: u8, rest: &[u8]) -> Vec<u8> {
        let mut bytes = vec![first, second];
        bytes.extend_from_slice(rest);
        bytes
    }

    fn payload_of(size: usize) -> Vec<u8> {
        (0..size)
            .map(|i| (i as u8).wrapping_mul(31).wrapping_add(7))
            .collect()
    }

    #[test]
    fn opcode_bytes_are_stable_and_the_reserved_set_is_closed() {
        let declared = [
            OpCode::CONTINUATION_BYTE,
            OpCode::TEXT_BYTE,
            OpCode::BINARY_BYTE,
            OpCode::CLOSE_BYTE,
            OpCode::PING_BYTE,
            OpCode::PONG_BYTE,
        ];
        for opcode in [
            OpCode::Continuation,
            OpCode::Text,
            OpCode::Binary,
            OpCode::Close,
            OpCode::Ping,
            OpCode::Pong,
        ] {
            assert_eq!(OpCode::from_byte(opcode.to_byte()), Some(opcode));
        }
        assert_eq!(OpCode::Text.to_byte(), 0x1);
        assert_eq!(OpCode::Close.to_byte(), 0x8);
        assert_eq!(OpCode::Ping.to_byte(), 0x9);
        assert_eq!(OpCode::Pong.to_byte(), 0xA);
        for byte in 0..=u8::MAX {
            assert_eq!(
                OpCode::from_byte(byte).is_some(),
                declared.contains(&byte),
                "declared state of opcode {byte:#04x}"
            );
        }
        assert!(!OpCode::Text.is_control());
        assert!(!OpCode::Continuation.is_control());
        assert!(OpCode::Close.is_control());
        assert!(OpCode::Ping.is_control());
        assert!(OpCode::Pong.is_control());
    }

    #[test]
    fn unmasked_text_hello_matches_the_known_wire_bytes() {
        let frame = Frame::new(OpCode::Text, b"Hello".to_vec());
        let bytes = encode(&frame, Direction::ToClient, None, LIMIT).unwrap();
        assert_eq!(bytes, vec![0x81, 0x05, 0x48, 0x65, 0x6c, 0x6c, 0x6f]);
        assert_eq!(decode(&bytes, Direction::ToClient, LIMIT), Ok(frame));
    }

    #[test]
    fn masked_text_hello_uses_the_repeating_four_byte_key() {
        let frame = Frame::new(OpCode::Text, b"Hello".to_vec());
        let bytes = encode(&frame, Direction::ToServer, Some(KEY), LIMIT).unwrap();
        // 0x81 FIN+text, 0x85 MASK+len 5, key 0a0b0c0d, then payload XOR key.
        assert_eq!(
            bytes,
            vec![0x81, 0x85, 0x0a, 0x0b, 0x0c, 0x0d, 0x42, 0x6e, 0x60, 0x61, 0x65]
        );
        assert_eq!(decode(&bytes, Direction::ToServer, LIMIT), Ok(frame));
    }

    #[test]
    fn masking_and_unmasking_are_the_same_xor_operation() {
        for size in [0, 1, 3, 4, 5, 9, 125, 300] {
            let payload = payload_of(size);
            let masked = mask(&payload, &KEY);
            if !payload.is_empty() {
                assert_ne!(masked, payload);
            }
            assert_eq!(mask(&masked, &KEY), payload);
        }
    }

    #[test]
    fn round_trip_covers_every_opcode_and_fin_flag_in_both_directions() {
        let opcodes = [
            OpCode::Continuation,
            OpCode::Text,
            OpCode::Binary,
            OpCode::Close,
            OpCode::Ping,
            OpCode::Pong,
        ];
        for opcode in opcodes {
            let finishes = if opcode.is_control() {
                vec![true]
            } else {
                vec![true, false]
            };
            for fin in finishes {
                let payload = payload_of(7);
                let frame = Frame {
                    fin,
                    opcode,
                    payload: payload.clone(),
                };
                let to_client = encode(&frame, Direction::ToClient, None, LIMIT).unwrap();
                assert_eq!(
                    decode(&to_client, Direction::ToClient, LIMIT),
                    Ok(frame.clone())
                );
                let to_server = encode(&frame, Direction::ToServer, Some(KEY), LIMIT).unwrap();
                assert_eq!(
                    decode(&to_server, Direction::ToServer, LIMIT),
                    Ok(frame.clone())
                );
            }
        }
    }

    #[test]
    fn boundary_lengths_select_the_shortest_form_and_round_trip() {
        // 125 is the 7-bit maximum, 126 the 16-bit minimum, 65535 the 16-bit maximum and
        // 65536 the 64-bit minimum. 127/255/256 sit either side of the byte boundary.
        for size in [0, 1, 125, 126, 127, 255, 256, 65535, 65536] {
            let payload = payload_of(size);
            let frame = Frame::new(OpCode::Binary, payload.clone());
            let bytes = encode(&frame, Direction::ToClient, None, LIMIT).unwrap();

            let expected_prefix: Vec<u8> = if size <= 125 {
                vec![0x82, size as u8]
            } else if size <= u16::MAX as usize {
                let mut p = vec![0x82, LEN_16];
                p.extend_from_slice(&(size as u16).to_be_bytes());
                p
            } else {
                let mut p = vec![0x82, LEN_64];
                p.extend_from_slice(&(size as u64).to_be_bytes());
                p
            };
            assert_eq!(
                &bytes[..expected_prefix.len()],
                &expected_prefix,
                "size {size}"
            );
            assert_eq!(bytes.len(), expected_prefix.len() + size);
            assert_eq!(
                decode(&bytes, Direction::ToClient, LIMIT),
                Ok(frame.clone())
            );

            let masked = encode(&frame, Direction::ToServer, Some(KEY), LIMIT).unwrap();
            assert_eq!(masked[1] & MASK, MASK, "size {size}");
            assert_eq!(decode(&masked, Direction::ToServer, LIMIT), Ok(frame));
        }
    }

    #[test]
    fn control_frames_accept_empty_and_maximum_size_payloads() {
        for opcode in [OpCode::Close, OpCode::Ping, OpCode::Pong] {
            for size in [0, MAX_CONTROL_PAYLOAD as usize] {
                let frame = Frame::new(opcode, payload_of(size));
                let bytes = encode(&frame, Direction::ToClient, None, LIMIT).unwrap();
                assert_eq!(
                    decode(&bytes, Direction::ToClient, LIMIT),
                    Ok(frame.clone())
                );
                let masked = encode(&frame, Direction::ToServer, Some(KEY), LIMIT).unwrap();
                assert_eq!(decode(&masked, Direction::ToServer, LIMIT), Ok(frame));
            }
        }
    }

    #[test]
    fn close_payload_status_code_is_opaque_to_the_codec() {
        // RFC 6455 lets a close frame carry a two-byte status code followed by a reason.
        // This codec preserves those bytes verbatim; interpretation is a layer above.
        let payload = vec![0x03, 0xe8, b'o', b'k'];
        let frame = Frame::new(OpCode::Close, payload);
        let bytes = encode(&frame, Direction::ToClient, None, LIMIT).unwrap();
        assert_eq!(bytes[2..], frame.payload[..]);
        assert_eq!(decode(&bytes, Direction::ToClient, LIMIT), Ok(frame));
    }

    #[test]
    fn encode_refuses_a_client_frame_without_a_mask_key() {
        let frame = Frame::new(OpCode::Text, vec![]);
        assert_eq!(
            encode(&frame, Direction::ToServer, None, LIMIT),
            Err(EncodeError::MissingMaskKey)
        );
    }

    #[test]
    fn encode_refuses_a_server_frame_with_a_mask_key() {
        let frame = Frame::new(OpCode::Text, vec![]);
        assert_eq!(
            encode(&frame, Direction::ToClient, Some(KEY), LIMIT),
            Err(EncodeError::UnexpectedMaskKey)
        );
    }

    #[test]
    fn encode_refuses_a_fragmented_control_frame() {
        let frame = Frame::fragment(OpCode::Ping, vec![]);
        assert_eq!(
            encode(&frame, Direction::ToClient, None, LIMIT),
            Err(EncodeError::FragmentedControl { code: 0x9 })
        );
    }

    #[test]
    fn encode_refuses_a_control_payload_over_125_bytes() {
        let frame = Frame::new(OpCode::Pong, vec![0; 126]);
        assert_eq!(
            encode(&frame, Direction::ToClient, None, LIMIT),
            Err(EncodeError::ControlPayloadTooLarge { length: 126 })
        );
    }

    #[test]
    fn encode_refuses_a_payload_over_the_caller_limit() {
        let tight = Limits {
            max_payload_bytes: 8,
        };
        let frame = Frame::new(OpCode::Binary, vec![0; 9]);
        assert_eq!(
            encode(&frame, Direction::ToClient, None, tight),
            Err(EncodeError::Oversize { length: 9, max: 8 })
        );
        let at_limit = Frame::new(OpCode::Binary, vec![0; 8]);
        let bytes = encode(&at_limit, Direction::ToClient, None, tight).unwrap();
        assert_eq!(decode(&bytes, Direction::ToClient, tight), Ok(at_limit));
    }

    #[test]
    fn encode_never_writes_a_length_the_wire_form_cannot_hold() {
        // The effective cap is min(limit, MAX_WIRE_LEN), so the 64-bit high bit is never set.
        let too_wide = Limits {
            max_payload_bytes: u64::MAX,
        };
        assert_eq!(effective_max(too_wide), MAX_WIRE_LEN);
    }

    #[test]
    fn decode_refuses_a_truncated_header() {
        for got in [0, 1] {
            assert_eq!(
                decode(&[0x81, 0x05][..got], Direction::ToClient, LIMIT),
                Err(DecodeError::TruncatedHeader { got })
            );
        }
    }

    #[test]
    fn decode_refuses_each_reserved_bit() {
        for rsv in [
            RSV1,
            RSV2,
            RSV3,
            RSV1 | RSV2,
            RSV1 | RSV3,
            RSV2 | RSV3,
            RSV_MASK,
        ] {
            assert_eq!(
                decode(
                    &wire(0x80 | rsv | 0x1, 0x0, &[]),
                    Direction::ToClient,
                    LIMIT
                ),
                Err(DecodeError::ReservedBitsSet { bits: rsv })
            );
        }
    }

    #[test]
    fn decode_refuses_every_reserved_opcode() {
        for code in [0x3, 0x4, 0x5, 0x6, 0x7, 0xB, 0xC, 0xD, 0xE, 0xF] {
            assert_eq!(
                decode(&wire(FIN | code, 0x0, &[]), Direction::ToClient, LIMIT),
                Err(DecodeError::ReservedOpcode { code })
            );
        }
    }

    #[test]
    fn decode_refuses_a_fragmented_control_frame() {
        for code in [OpCode::CLOSE_BYTE, OpCode::PING_BYTE, OpCode::PONG_BYTE] {
            assert_eq!(
                decode(&wire(code, 0x0, &[]), Direction::ToClient, LIMIT),
                Err(DecodeError::FragmentedControl { code })
            );
        }
    }

    #[test]
    fn decode_enforces_the_masking_rule_for_each_direction() {
        // Client-to-server requires MASK=1.
        assert_eq!(
            decode(&wire(0x81, 0x00, &[]), Direction::ToServer, LIMIT),
            Err(DecodeError::WrongMaskBit {
                direction: Direction::ToServer,
                masked: false
            })
        );
        // Server-to-client requires MASK=0.
        assert_eq!(
            decode(&wire(0x81, MASK, &[0, 0, 0, 0]), Direction::ToClient, LIMIT),
            Err(DecodeError::WrongMaskBit {
                direction: Direction::ToClient,
                masked: true
            })
        );
    }

    #[test]
    fn decode_refuses_truncated_extended_length_fields() {
        for got in [0, 1] {
            assert_eq!(
                decode(
                    &wire(0x82, LEN_16, &[0x01][..got]),
                    Direction::ToClient,
                    LIMIT
                ),
                Err(DecodeError::TruncatedExtendedLength {
                    form: LEN_16,
                    need: 2,
                    got
                })
            );
        }
        for got in 0..8 {
            assert_eq!(
                decode(
                    &wire(0x82, LEN_64, &[0u8; 8][..got]),
                    Direction::ToClient,
                    LIMIT
                ),
                Err(DecodeError::TruncatedExtendedLength {
                    form: LEN_64,
                    need: 8,
                    got
                })
            );
        }
    }

    #[test]
    fn decode_refuses_a_64_bit_length_with_the_high_bit_set() {
        let mut ext = (u16::MAX as u64 + 1).to_be_bytes().to_vec();
        ext[0] |= 0x80;
        let raw = u64::from_be_bytes(ext.clone().try_into().unwrap());
        assert_eq!(
            decode(&wire(0x82, LEN_64, &ext), Direction::ToClient, LIMIT),
            Err(DecodeError::Invalid64BitLength { raw })
        );
    }

    #[test]
    fn decode_refuses_non_minimal_length_forms() {
        assert_eq!(
            decode(
                &wire(0x82, LEN_16, &5u16.to_be_bytes()),
                Direction::ToClient,
                LIMIT
            ),
            Err(DecodeError::NonMinimalLength {
                form: LEN_16,
                length: 5
            })
        );
        assert_eq!(
            decode(
                &wire(0x82, LEN_64, &7u64.to_be_bytes()),
                Direction::ToClient,
                LIMIT
            ),
            Err(DecodeError::NonMinimalLength {
                form: LEN_64,
                length: 7
            })
        );
        assert_eq!(
            decode(
                &wire(0x82, LEN_64, &300u64.to_be_bytes()),
                Direction::ToClient,
                LIMIT
            ),
            Err(DecodeError::NonMinimalLength {
                form: LEN_64,
                length: 300
            })
        );
    }

    #[test]
    fn decode_refuses_a_control_payload_over_125_bytes() {
        assert_eq!(
            decode(
                &wire(0x89, LEN_16, &126u16.to_be_bytes()),
                Direction::ToClient,
                LIMIT
            ),
            Err(DecodeError::ControlPayloadTooLarge { length: 126 })
        );
    }

    #[test]
    fn decode_refuses_an_oversize_declared_length_before_reading_the_payload() {
        // Only a header is present; the declared length alone triggers the refusal,
        // before any mask key or payload is read and without a large allocation.
        let small = Limits {
            max_payload_bytes: 1024,
        };
        let length = 4096u64;
        assert_eq!(
            decode(
                &wire(0x82, LEN_16, &4096u16.to_be_bytes()),
                Direction::ToClient,
                small
            ),
            Err(DecodeError::Oversize { length, max: 1024 })
        );
        let tight = Limits {
            max_payload_bytes: 4,
        };
        assert_eq!(
            decode(&wire(0x82, 0x05, &[0; 5]), Direction::ToClient, tight),
            Err(DecodeError::Oversize { length: 5, max: 4 })
        );
    }

    #[test]
    fn decode_refuses_a_truncated_mask_key() {
        for got in 0..MASK_KEY_LEN {
            // The frame declares one payload byte but carries only `got` key bytes.
            let rest = &[0xab, 0xcd, 0xef][..got];
            assert_eq!(
                decode(&wire(0x81, MASK | 0x01, rest), Direction::ToServer, LIMIT),
                Err(DecodeError::TruncatedMaskKey { got })
            );
        }
    }

    #[test]
    fn decode_refuses_a_truncated_payload() {
        assert_eq!(
            decode(&wire(0x82, 0x04, &[1, 2, 3]), Direction::ToClient, LIMIT),
            Err(DecodeError::TruncatedPayload {
                declared: 4,
                available: 3
            })
        );
        assert_eq!(
            decode(
                &wire(0x81, MASK | 0x01, &[0, 0, 0, 0]),
                Direction::ToServer,
                LIMIT
            ),
            Err(DecodeError::TruncatedPayload {
                declared: 1,
                available: 0
            })
        );
    }

    #[test]
    fn decode_refuses_trailing_bytes() {
        let mut bytes = encode(
            &Frame::new(OpCode::Text, vec![0xab]),
            Direction::ToClient,
            None,
            LIMIT,
        )
        .unwrap();
        bytes.push(0x00);
        assert_eq!(
            decode(&bytes, Direction::ToClient, LIMIT),
            Err(DecodeError::TrailingBytes {
                declared: 1,
                available: 2
            })
        );

        // A second, fully valid frame is still surplus data the decoder may not drop.
        let mut two = encode(
            &Frame::new(OpCode::Text, vec![]),
            Direction::ToClient,
            None,
            LIMIT,
        )
        .unwrap();
        two.extend(
            encode(
                &Frame::new(OpCode::Ping, vec![]),
                Direction::ToClient,
                None,
                LIMIT,
            )
            .unwrap(),
        );
        assert_eq!(
            decode(&two, Direction::ToClient, LIMIT),
            Err(DecodeError::TrailingBytes {
                declared: 0,
                available: 2
            })
        );
    }

    #[test]
    fn check_order_is_fixed_for_competing_faults() {
        // A short header wins over the bad bytes it does contain.
        assert_eq!(
            decode(&[0xff], Direction::ToClient, LIMIT),
            Err(DecodeError::TruncatedHeader { got: 1 })
        );
        // Reserved bits win over a reserved opcode.
        assert_eq!(
            decode(&wire(RSV1 | 0x3, 0x0, &[]), Direction::ToClient, LIMIT),
            Err(DecodeError::ReservedBitsSet { bits: RSV1 })
        );
        // A reserved opcode wins over a fragmented control frame and a wrong MASK bit.
        assert_eq!(
            decode(&wire(0xB, MASK, &[]), Direction::ToClient, LIMIT),
            Err(DecodeError::ReservedOpcode { code: 0xB })
        );
        // A fragmented control frame wins over a wrong MASK bit.
        assert_eq!(
            decode(
                &wire(OpCode::PING_BYTE, MASK, &[]),
                Direction::ToClient,
                LIMIT
            ),
            Err(DecodeError::FragmentedControl { code: 0x9 })
        );
        // A wrong MASK bit wins over a truncated extended length.
        assert_eq!(
            decode(&wire(0x82, LEN_16, &[]), Direction::ToServer, LIMIT),
            Err(DecodeError::WrongMaskBit {
                direction: Direction::ToServer,
                masked: false
            })
        );
        // A truncated extended length wins over an oversize declared length.
        assert_eq!(
            decode(&wire(0x82, LEN_64, &[0xff; 7]), Direction::ToClient, LIMIT),
            Err(DecodeError::TruncatedExtendedLength {
                form: LEN_64,
                need: 8,
                got: 7
            })
        );
        // The caller limit wins over a missing payload.
        assert_eq!(
            decode(
                &wire(0x82, 0x05, &[]),
                Direction::ToClient,
                Limits {
                    max_payload_bytes: 4
                }
            ),
            Err(DecodeError::Oversize { length: 5, max: 4 })
        );
    }
}
