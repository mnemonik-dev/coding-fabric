import * as cbor from "cbor";
import * as readline from "readline";

interface TestVector {
  version: number;
  nonce: number;
}

async function main() {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  let input = "";
  for await (const line of rl) {
    input += line;
  }

  const value: TestVector = JSON.parse(input);

  const encoded = cbor.encode(value);
  const hex = encoded.toString("hex");

  console.log(hex);
}

main().catch(console.error);
