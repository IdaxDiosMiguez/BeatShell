//! Localiza un binario de ffmpeg.
//!
//! Orden: `BEATSHELL_FFMPEG`, el `ffmpeg` del sistema y, si no, el que empaqueta
//! `imageio-ffmpeg` dentro del entorno de la versión Python (si está instalado).

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

fn exe_name() -> &'static str {
    if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    }
}

pub fn which(name: &str) -> Option<PathBuf> {
    let paths = std::env::var_os("PATH")?;
    std::env::split_paths(&paths)
        .map(|d| d.join(name))
        .find(|p| p.is_file())
}

pub fn ffmpeg_exe() -> Option<&'static Path> {
    static EXE: OnceLock<Option<PathBuf>> = OnceLock::new();
    EXE.get_or_init(|| {
        if let Some(p) = std::env::var_os("BEATSHELL_FFMPEG")
            .map(PathBuf::from)
            .filter(|p| p.is_file())
        {
            return Some(p);
        }
        which(exe_name())
    })
    .as_deref()
}

pub const NOT_FOUND: &str = "No se encontró ffmpeg. Instálalo (https://ffmpeg.org) o indica su ruta en \
                             la variable BEATSHELL_FFMPEG.";

/// `Command` que en Windows no abre una ventana de consola.
pub fn command(exe: &Path) -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new(exe);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}
