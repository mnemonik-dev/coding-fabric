use serde::{Deserialize, Serialize};
use std::io::Cursor;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TestVector {
    pub version: u8,
    pub nonce: u64,
}

pub fn serialize_to_cbor(value: &TestVector) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut vec = Vec::new();
    ciborium::ser::into_writer(value, &mut vec)?;
    Ok(vec)
}

pub fn deserialize_from_cbor(data: &[u8]) -> Result<TestVector, Box<dyn std::error::Error>> {
    let cursor = Cursor::new(data);
    let value: TestVector = ciborium::de::from_reader(cursor)?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_round_trip() {
        let original = TestVector {
            version: 1,
            nonce: 42,
        };
        let encoded = serialize_to_cbor(&original).expect("encode failed");
        let decoded = deserialize_from_cbor(&encoded).expect("decode failed");
        assert_eq!(original, decoded);
    }
}
