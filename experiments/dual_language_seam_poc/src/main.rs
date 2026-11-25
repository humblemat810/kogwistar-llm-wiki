use std::io::{self, BufRead, Write};

use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};

const CONTRACT_VERSION: u32 = 1;
const DEFAULT_SEED: u64 = 0x4b4f_4757_4953_5441;

#[derive(Clone, Debug, Deserialize)]
struct Request {
    contract_version: u32,
    operation: String,
    #[serde(default)]
    count: usize,
    #[serde(default)]
    account_count: usize,
    #[serde(default)]
    batch_size: usize,
    #[serde(default = "default_seed")]
    seed: u64,
    #[serde(default)]
    transfers: Vec<Transfer>,
    #[serde(default)]
    callback_id: String,
    #[serde(default)]
    callback_values: Vec<f64>,
}

#[derive(Clone, Debug, Deserialize)]
struct Transfer {
    from: usize,
    to: usize,
    amount: i64,
}

#[derive(Debug, Serialize)]
struct Snapshot {
    contract_version: u32,
    balances: Vec<i64>,
    transfer_count: i64,
    total_cents: i64,
    trace_digest: String,
}

fn default_seed() -> u64 {
    DEFAULT_SEED
}

fn initialized_db(account_count: usize) -> Result<Connection, String> {
    if account_count < 2 {
        return Err("account_count must be at least two".to_owned());
    }
    let connection = Connection::open_in_memory().map_err(|error| error.to_string())?;
    connection
        .execute_batch(
            "CREATE TABLE accounts(id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
             CREATE TABLE transfers(id INTEGER PRIMARY KEY, from_id INTEGER NOT NULL,
                 to_id INTEGER NOT NULL, amount INTEGER NOT NULL);",
        )
        .map_err(|error| error.to_string())?;
    for id in 0..account_count {
        connection
            .execute(
                "INSERT INTO accounts(id, balance) VALUES (?1, 1000000)",
                [id as i64],
            )
            .map_err(|error| error.to_string())?;
    }
    Ok(connection)
}

fn apply_batch(connection: &mut Connection, transfers: &[Transfer]) -> Result<(), String> {
    let transaction = connection
        .transaction()
        .map_err(|error| error.to_string())?;
    for transfer in transfers {
        if transfer.amount <= 0 || transfer.from == transfer.to {
            return Err("transfer must be positive and accounts must differ".to_owned());
        }
        let debited = transaction
            .execute(
                "UPDATE accounts SET balance = balance - ?1
                 WHERE id = ?2 AND balance >= ?1",
                params![transfer.amount, transfer.from as i64],
            )
            .map_err(|error| error.to_string())?;
        if debited != 1 {
            return Err("unknown account or insufficient funds".to_owned());
        }
        let credited = transaction
            .execute(
                "UPDATE accounts SET balance = balance + ?1 WHERE id = ?2",
                params![transfer.amount, transfer.to as i64],
            )
            .map_err(|error| error.to_string())?;
        if credited != 1 {
            return Err("unknown destination account".to_owned());
        }
        transaction
            .execute(
                "INSERT INTO transfers(from_id, to_id, amount) VALUES (?1, ?2, ?3)",
                params![transfer.from as i64, transfer.to as i64, transfer.amount],
            )
            .map_err(|error| error.to_string())?;
    }
    transaction.commit().map_err(|error| error.to_string())
}

fn generated_transfers(count: usize, account_count: usize, seed: u64) -> Vec<Transfer> {
    let mut state = seed;
    let mut next = || {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        state >> 32
    };
    (0..count)
        .map(|_| {
            let from = next() as usize % account_count;
            let mut to = next() as usize % account_count;
            if to == from {
                to = (to + 1) % account_count;
            }
            let amount = (next() % 5 + 1) as i64;
            Transfer { from, to, amount }
        })
        .collect()
}

fn snapshot(connection: &Connection) -> Result<Snapshot, String> {
    let mut statement = connection
        .prepare("SELECT balance FROM accounts ORDER BY id")
        .map_err(|error| error.to_string())?;
    let balances = statement
        .query_map([], |row| row.get::<_, i64>(0))
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    let transfer_count = connection
        .query_row("SELECT COUNT(*) FROM transfers", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let total_cents = balances.iter().sum();
    let mut trace_hash = 0xcbf29ce484222325_u64;
    let mut trace_statement = connection
        .prepare("SELECT from_id, to_id, amount FROM transfers ORDER BY id")
        .map_err(|error| error.to_string())?;
    let trace_rows = trace_statement
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, i64>(2)?,
            ))
        })
        .map_err(|error| error.to_string())?;
    for row in trace_rows {
        let (from, to, amount) = row.map_err(|error| error.to_string())?;
        for byte in from
            .to_le_bytes()
            .into_iter()
            .chain(to.to_le_bytes())
            .chain(amount.to_le_bytes())
        {
            trace_hash = (trace_hash ^ u64::from(byte)).wrapping_mul(0x100000001b3);
        }
    }
    Ok(Snapshot {
        contract_version: CONTRACT_VERSION,
        balances,
        transfer_count,
        total_cents,
        trace_digest: format!("{trace_hash:016x}"),
    })
}

fn handle(
    connection: &mut Option<Connection>,
    request: Request,
) -> Result<serde_json::Value, String> {
    if request.contract_version != CONTRACT_VERSION {
        return Err(format!(
            "unsupported contract_version {}",
            request.contract_version
        ));
    }
    match request.operation.as_str() {
        "reset" => {
            *connection = Some(initialized_db(request.account_count)?);
            Ok(serde_json::json!({"contract_version": CONTRACT_VERSION, "ready": true}))
        }
        "apply" => {
            let db = connection
                .as_mut()
                .ok_or_else(|| "send reset before apply".to_owned())?;
            apply_batch(db, &request.transfers)?;
            Ok(
                serde_json::json!({"contract_version": CONTRACT_VERSION, "applied": request.transfers.len()}),
            )
        }
        "run_seeded" => {
            if request.batch_size == 0 || request.account_count < 2 {
                return Err("batch_size must be positive and account_count at least two".to_owned());
            }
            *connection = Some(initialized_db(request.account_count)?);
            let transfers = generated_transfers(request.count, request.account_count, request.seed);
            let db = connection.as_mut().expect("database initialized above");
            for batch in transfers.chunks(request.batch_size) {
                apply_batch(db, batch)?;
            }
            Ok(
                serde_json::json!({"contract_version": CONTRACT_VERSION, "applied": transfers.len()}),
            )
        }
        "snapshot" => {
            let db = connection
                .as_ref()
                .ok_or_else(|| "send reset before snapshot".to_owned())?;
            serde_json::to_value(snapshot(db)?).map_err(|error| error.to_string())
        }
        _ => Err(format!("unknown operation {:?}", request.operation)),
    }
}

fn python_step<R: BufRead, W: Write>(
    reader: &mut R,
    writer: &mut W,
    request: &Request,
) -> Result<serde_json::Value, String> {
    if request.callback_id.is_empty() || request.callback_values.len() > 100_000 {
        return Err("callback id is required and values are capped at 100000".to_owned());
    }
    serde_json::to_writer(
        &mut *writer,
        &serde_json::json!({
            "contract_version": CONTRACT_VERSION,
            "kind": "python_callback_request",
            "callback_id": request.callback_id,
            "operation": "mean_and_l2_norm",
            "values": request.callback_values,
        }),
    )
    .map_err(|error| error.to_string())?;
    writer.write_all(b"\n").map_err(|error| error.to_string())?;
    writer.flush().map_err(|error| error.to_string())?;

    let mut reply_line = String::new();
    reader
        .read_line(&mut reply_line)
        .map_err(|error| error.to_string())?;
    if reply_line.is_empty() {
        return Err("Python worker closed before callback response".to_owned());
    }
    let reply: serde_json::Value = serde_json::from_str(&reply_line)
        .map_err(|error| format!("invalid callback response: {error}"))?;
    if reply
        .get("contract_version")
        .and_then(|value| value.as_u64())
        != Some(u64::from(CONTRACT_VERSION))
        || reply.get("kind").and_then(|value| value.as_str()) != Some("python_callback_result")
        || reply.get("callback_id").and_then(|value| value.as_str())
            != Some(request.callback_id.as_str())
    {
        return Err("callback response contract or correlation id mismatch".to_owned());
    }
    let result = reply
        .get("result")
        .and_then(|value| value.as_object())
        .ok_or_else(|| "callback result must be an object".to_owned())?;
    if !result.get("mean").is_some_and(serde_json::Value::is_number)
        || !result
            .get("l2_norm")
            .is_some_and(serde_json::Value::is_number)
    {
        return Err("callback result is missing numeric mean or l2_norm".to_owned());
    }
    Ok(serde_json::json!({
        "contract_version": CONTRACT_VERSION,
        "callback_id": request.callback_id,
        "result": result,
    }))
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut reader = stdin.lock();
    let mut writer = stdout.lock();
    let mut connection = None;
    loop {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {}
            Err(error) => {
                eprintln!("input error: {error}");
                break;
            }
        }
        let response = match serde_json::from_str::<Request>(&line) {
            Ok(request) if request.operation == "python_step" => {
                python_step(&mut reader, &mut writer, &request)
            }
            Ok(request) => handle(&mut connection, request),
            Err(error) => Err(format!("invalid request: {error}")),
        };
        let value = match response {
            Ok(value) => value,
            Err(error) => serde_json::json!({"contract_version": CONTRACT_VERSION, "error": error}),
        };
        if serde_json::to_writer(&mut writer, &value).is_err()
            || writer.write_all(b"\n").is_err()
            || writer.flush().is_err()
        {
            break;
        }
    }
}
