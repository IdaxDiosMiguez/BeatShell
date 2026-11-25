//! Rutas de datos por sistema operativo y configuración persistente.
//!
//! Usa las mismas carpetas que la versión Python (platformdirs) para que ambas
//! compartan `config.json`, `playlists.json`, `library.json` y `session.json`.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub const APP_NAME: &str = "BeatShell";

pub struct Dirs {
    pub data: PathBuf,
    pub cache: PathBuf,
}

/// `BEATSHELL_HOME` permite usar una carpeta de datos alternativa (pruebas, modo portable).
pub fn dirs() -> &'static Dirs {
    static D: OnceLock<Dirs> = OnceLock::new();
    D.get_or_init(|| {
        let d = match std::env::var_os("BEATSHELL_HOME") {
            Some(home) => {
                let home = PathBuf::from(home);
                Dirs {
                    cache: home.join("cache"),
                    data: home,
                }
            }
            None => {
                // platformdirs: user_data_dir(appauthor=False) == data_local_dir en los tres sistemas.
                let base = dirs::data_local_dir().unwrap_or_else(|| PathBuf::from("."));
                let cache_base = dirs::cache_dir().unwrap_or_else(|| PathBuf::from("."));
                let cache = if cfg!(windows) {
                    cache_base.join(APP_NAME).join("Cache")
                } else {
                    cache_base.join(APP_NAME)
                };
                Dirs {
                    data: base.join(APP_NAME),
                    cache,
                }
            }
        };
        let _ = fs::create_dir_all(&d.data);
        let _ = fs::create_dir_all(d.cache.join("covers"));
        d
    })
}

pub fn data_path(name: &str) -> PathBuf {
    dirs().data.join(name)
}

pub fn default_download_dir() -> String {
    let music = dirs::audio_dir()
        .or_else(|| dirs::home_dir().map(|h| h.join("Music")))
        .unwrap_or_else(|| PathBuf::from("Music"));
    music.join(APP_NAME).to_string_lossy().into_owned()
}

/// Escribe JSON sin dejar archivos corruptos si el proceso muere a mitad.
/// Mismo formato que Python: `indent=1`, sin escapar caracteres no ASCII.
pub fn write_json_atomic<T: Serialize>(path: &Path, data: &T) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let mut buf = Vec::new();
    let fmt = serde_json::ser::PrettyFormatter::with_indent(b" ");
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, fmt);
    data.serialize(&mut ser).map_err(std::io::Error::other)?;
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("data");
    let tmp = path.with_file_name(format!("{name}.{}.tmp", crate::models::new_uid(8)));
    let res = (|| {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(&buf)?;
        f.sync_all()?;
        fs::rename(&tmp, path)
    })();
    if res.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    res
}

pub fn read_json(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub volume: i64,
    pub shuffle: bool,
    /// off | all | one
    pub repeat: String,
    pub theme: String,
    pub download_dir: String,
    /// "0" = VBR V0 (~245 kbps) · "320" = CBR 320 kbps
    pub mp3_quality: String,
    pub concurrent_downloads: i64,
    pub pixel_covers: bool,
    /// auto | show | hide
    pub sidebar: String,
    pub eq_preset: String,
    pub dj_mode: bool,
    /// segundos de fundido entre canciones en modo DJ
    pub dj_crossfade: f64,
    /// auto | kitty | bloques
    pub cover_renderer: String,
    pub spotify_client_id: String,
    pub spotify_client_secret: String,
    /// Claves que esta versión no conoce: se conservan al guardar.
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            volume: 70,
            shuffle: false,
            repeat: "off".into(),
            theme: "beatshell".into(),
            download_dir: default_download_dir(),
            mp3_quality: "0".into(),
            concurrent_downloads: 2,
            pixel_covers: true,
            sidebar: "auto".into(),
            eq_preset: "Plano".into(),
            dj_mode: false,
            dj_crossfade: 8.0,
            cover_renderer: "auto".into(),
            spotify_client_id: String::new(),
            spotify_client_secret: String::new(),
            extra: Map::new(),
        }
    }
}

impl Config {
    pub fn path() -> PathBuf {
        data_path("config.json")
    }

    pub fn load() -> Config {
        Self::from_value(read_json(&Self::path()).unwrap_or(Value::Null))
    }

    /// Tolerante como la versión Python: un valor con tipo incorrecto se ignora
    /// (se queda el predeterminado) en lugar de invalidar todo el archivo.
    pub fn from_value(raw: Value) -> Config {
        let Value::Object(raw) = raw else {
            return Config::default();
        };
        let mut merged = match serde_json::to_value(Config::default()) {
            Ok(Value::Object(m)) => m,
            _ => Map::new(),
        };
        for (k, mut v) in raw {
            // Python hace int(cfg.volume): acepta 70.0
            if k == "volume"
                && let Some(f) = v.as_f64()
            {
                v = Value::from(f as i64);
            }
            let prev = merged.insert(k.clone(), v);
            if serde_json::from_value::<Config>(Value::Object(merged.clone())).is_err() {
                match prev {
                    Some(p) => merged.insert(k, p),
                    None => merged.remove(&k),
                };
            }
        }
        let mut cfg: Config = serde_json::from_value(Value::Object(merged)).unwrap_or_default();
        cfg.volume = cfg.volume.clamp(0, 100);
        if !["off", "all", "one"].contains(&cfg.repeat.as_str()) {
            cfg.repeat = "off".into();
        }
        cfg
    }

    pub fn save(&self) -> std::io::Result<()> {
        write_json_atomic(&Self::path(), self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tolerant_load() {
        let raw = serde_json::json!({
            "volume": 150.0, "repeat": "nope", "shuffle": "sí", "theme": "gameboy",
            "futura_opcion": [1, 2]
        });
        let c = Config::from_value(raw);
        assert_eq!(c.volume, 100);
        assert_eq!(c.repeat, "off");
        assert!(!c.shuffle);
        assert_eq!(c.theme, "gameboy");
        let out = serde_json::to_value(&c).unwrap();
        assert_eq!(out["futura_opcion"], serde_json::json!([1, 2]));
        assert_eq!(out["eq_preset"], "Plano");
    }

    #[test]
    fn python_format() {
        let dir = std::env::temp_dir().join(format!("bs-test-{}", crate::models::new_uid(6)));
        let p = dir.join("x.json");
        write_json_atomic(&p, &serde_json::json!({"a": "canción", "b": [1]})).unwrap();
        let s = fs::read_to_string(&p).unwrap();
        assert_eq!(s, "{\n \"a\": \"canción\",\n \"b\": [\n  1\n ]\n}");
        let _ = fs::remove_dir_all(dir);
    }
}
