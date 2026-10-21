use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::io::{self, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

fn usage() -> ! {
    eprintln!("usage: brandfast list <root> <brand|fork> | brandfast tree-hash <root>");
    std::process::exit(2);
}

fn should_skip_dir(name: &str, mode: &str, at_root: bool) -> bool {
    if name == ".git" {
        return true;
    }
    if mode == "brand" && at_root && (name == "hermes-agent" || name.starts_with("Nastech-Update#"))
    {
        return true;
    }
    if mode == "fork" && matches!(name, "node_modules" | "__pycache__" | ".venv" | "venv") {
        return true;
    }
    false
}

fn collect_files(
    root: &Path,
    mode: &str,
    current: &Path,
    at_root: bool,
    out: &mut Vec<String>,
) -> io::Result<()> {
    let mut entries: Vec<_> = fs::read_dir(current)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry.file_name().to_string_lossy().into_owned();
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)?;
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() {
            if should_skip_dir(&name, mode, at_root) {
                continue;
            }
            collect_files(root, mode, &path, false, out)?;
        } else if file_type.is_file() {
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            out.push(rel);
        }
    }
    Ok(())
}

fn list(root: &Path, mode: &str) -> io::Result<()> {
    if mode != "brand" && mode != "fork" {
        usage();
    }
    let mut files = Vec::new();
    collect_files(root, mode, root, true, &mut files)?;
    files.sort();
    let stdout = io::stdout();
    let mut output = BufWriter::new(stdout.lock());
    for file in files {
        if let Err(error) = writeln!(output, "{file}") {
            return if error.kind() == io::ErrorKind::BrokenPipe {
                Ok(())
            } else {
                Err(error)
            };
        }
    }
    Ok(())
}

fn sha256_file(path: &Path) -> io::Result<String> {
    let mut file = fs::File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn tree_hash(root: &Path) -> io::Result<()> {
    let mut files = Vec::new();
    collect_files(root, "all", root, true, &mut files)?;
    files.sort();
    let stdout = io::stdout();
    let mut output = BufWriter::new(stdout.lock());
    for relative in files {
        let path = root.join(&relative);
        let metadata = fs::symlink_metadata(&path)?;
        #[cfg(unix)]
        let mode = {
            use std::os::unix::fs::PermissionsExt;
            metadata.permissions().mode() & 0o7777
        };
        #[cfg(not(unix))]
        let mode = 0;
        if let Err(error) = writeln!(output, "{relative}\t{mode:o}\t{}", sha256_file(&path)?) {
            return if error.kind() == io::ErrorKind::BrokenPipe {
                Ok(())
            } else {
                Err(error)
            };
        }
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 || args.len() > 4 {
        usage();
    }
    let command = &args[1];
    let root = PathBuf::from(&args[2]);
    let result = match command.as_str() {
        "list" => {
            if args.len() != 4 {
                usage();
            }
            list(&root, &args[3])
        }
        "tree-hash" => tree_hash(&root),
        _ => usage(),
    };
    if let Err(error) = result {
        eprintln!("brandfast: {error}");
        std::process::exit(1);
    }
}
