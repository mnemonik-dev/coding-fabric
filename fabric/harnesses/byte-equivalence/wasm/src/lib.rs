use wasm_bindgen::prelude::*;
use serde::{Deserialize, Serialize};

#[wasm_bindgen]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TestVector {
    pub version: u8,
    pub nonce: u64,
}

#[wasm_bindgen]
pub fn serialize_cbor(version: u8, nonce: u64) -> String {
    let value = TestVector { version, nonce };
    match ciborium::to_vec(&value) {
        Ok(bytes) => hex::encode(&bytes),
        Err(_) => String::from("error"),
    }
}

#[wasm_bindgen]
pub fn deserialize_cbor(hex_string: &str) -> String {
    if let Ok(bytes) = hex::decode(hex_string) {
        match ciborium::from_slice::<TestVector>(&bytes) {
            Ok(value) => format!("v{}n{}", value.version, value.nonce),
            Err(_) => String::from("decode_error"),
        }
    } else {
        String::from("hex_error")
    }
}
