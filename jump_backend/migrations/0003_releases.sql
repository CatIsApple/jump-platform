-- Releases registry: maps versions to R2 object keys for auto-update.
-- Inserted by GitHub Actions after each successful build upload.

CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version TEXT NOT NULL,           -- "0.8.0", "0.8.1" (no "v" prefix)
  platform TEXT NOT NULL,          -- "windows" | "macos"
  r2_key TEXT NOT NULL,            -- "releases/v0.8.0/GUARDIAN_Jump_Setup.exe"
  filename TEXT NOT NULL,          -- "GUARDIAN_Jump_Setup.exe"
  size INTEGER NOT NULL,           -- bytes
  sha256 TEXT NOT NULL,            -- hex
  notes TEXT NOT NULL DEFAULT '',  -- release notes
  released_at INTEGER NOT NULL,    -- unix seconds
  is_published INTEGER NOT NULL DEFAULT 1,  -- 0 to disable distribution
  UNIQUE(version, platform)
);

CREATE INDEX IF NOT EXISTS idx_releases_published_platform
  ON releases(is_published, platform, released_at DESC);

-- Update download log (audit trail).
CREATE TABLE IF NOT EXISTS update_downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id INTEGER NOT NULL,
  license_id INTEGER NOT NULL,
  device_id TEXT NOT NULL DEFAULT '',
  bytes_sent INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT '',  -- "ok" | "failed"
  downloaded_at INTEGER NOT NULL,
  FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE,
  FOREIGN KEY (license_id) REFERENCES licenses(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_update_downloads_license
  ON update_downloads(license_id, downloaded_at DESC);
