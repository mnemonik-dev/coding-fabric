use std::io::{self, Read};
use mnemonic_serializer::{TestVector, serialize_to_cbor, deserialize_from_cbor};

fn main() -> io::Result<()> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;

    let value: TestVector = serde_json::from_str(&input)
        .expect("Failed to parse JSON input");

    let cbor_bytes = serialize_to_cbor(&value)
        .expect("Failed to serialize to CBOR");

    let hex = hex::encode(&cbor_bytes);
    println!("{}", hex);

    Ok(())
}
