use std::fs::OpenOptions;
use std::io::Write;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

static LOG: Mutex<Option<std::fs::File>> = Mutex::new(None);

pub fn init() {
    let _ = std::fs::create_dir_all("D:\\monkey-pet\\logs");
    if let Ok(f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("D:\\monkey-pet\\logs\\crash.log")
    {
        if let Ok(mut g) = LOG.lock() {
            *g = Some(f);
        }
        log("LOG STARTED");
    }
}

pub fn log(msg: &str) {
    if let Ok(mut g) = LOG.lock() {
        if let Some(f) = g.as_mut() {
            let ts = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis();
            let _ = writeln!(f, "[{}] {}", ts, msg);
            let _ = f.flush();
        }
    }
}
