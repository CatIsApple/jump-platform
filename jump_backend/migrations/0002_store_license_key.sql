-- Add plaintext license_key column to licenses table
-- SECURITY NOTE: This stores the full license key in plaintext for admin recovery.
-- Existing rows will have NULL for this column (not recoverable).

ALTER TABLE licenses ADD COLUMN license_key TEXT;
