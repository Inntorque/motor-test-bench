package main

import (
	"archive/zip"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
)

const launcherVersion = 1

var versionPattern = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)
var hashPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var reservedPath = regexp.MustCompile(`^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$`)

type envelope struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}
type release struct {
	Schema        int    `json:"schema"`
	Version       string `json:"version"`
	Platform      string `json:"platform"`
	Arch          string `json:"arch"`
	SHA256        string `json:"sha256"`
	Size          int64  `json:"size"`
	URL           string `json:"url"`
	Notes         string `json:"notes"`
	MinLauncher   int    `json:"minLauncher"`
	ProtocolMajor int    `json:"protocolMajor"`
	DataSchema    int    `json:"dataSchema"`
}
type activeState struct {
	Version  string `json:"version"`
	Previous string `json:"previous,omitempty"`
	Pending  bool   `json:"pending,omitempty"`
	Backup   string `json:"backup,omitempty"`
}

func newer(next, current string) bool {
	if !versionPattern.MatchString(next) || !versionPattern.MatchString(current) {
		return false
	}
	a, b := strings.Split(next, "."), strings.Split(current, ".")
	for i := range a {
		x, e1 := strconv.ParseUint(a[i], 10, 32)
		y, e2 := strconv.ParseUint(b[i], 10, 32)
		if e1 != nil || e2 != nil {
			return false
		}
		if x != y {
			return x > y
		}
	}
	return false
}
func verifyRelease(data, key []byte) (release, error) {
	var result release
	var wrapped envelope
	if len(data) > 1024*1024 {
		return result, errors.New("manifest too large")
	}
	if err := json.Unmarshal(data, &wrapped); err != nil {
		return result, err
	}
	payload, err := base64.StdEncoding.DecodeString(wrapped.Payload)
	if err != nil {
		return result, err
	}
	signature, err := base64.StdEncoding.DecodeString(wrapped.Signature)
	if err != nil {
		return result, err
	}
	block, _ := pem.Decode(key)
	if block == nil {
		return result, errors.New("missing release public key")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return result, err
	}
	public, ok := parsed.(ed25519.PublicKey)
	if !ok || !ed25519.Verify(public, payload, signature) {
		return result, errors.New("invalid release signature")
	}
	if err = json.Unmarshal(payload, &result); err != nil {
		return result, err
	}
	platform := runtime.GOOS
	if platform == "windows" {
		platform = "win32"
	}
	if result.Schema != 1 || !versionPattern.MatchString(result.Version) || result.Platform != platform || result.Arch != "x64" || runtime.GOARCH != "amd64" || !hashPattern.MatchString(result.SHA256) || result.Size <= 0 || result.Size > 2<<30 || result.MinLauncher < 1 || result.MinLauncher > launcherVersion || result.ProtocolMajor != 1 || result.DataSchema != 3 {
		return result, errors.New("incompatible release manifest")
	}
	return result, nil
}
func writeJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path+".tmp", os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return replaceFile(path+".tmp", path)
}
func readActive(root string) (activeState, error) {
	var state activeState
	data, err := os.ReadFile(filepath.Join(root, "active.json"))
	if err != nil {
		return state, err
	}
	if err = json.Unmarshal(data, &state); err != nil {
		return state, err
	}
	if !versionPattern.MatchString(state.Version) || state.Previous != "" && !versionPattern.MatchString(state.Previous) {
		return state, errors.New("invalid active version")
	}
	if state.Pending && (state.Previous == "" || state.Backup != "config-"+state.Version) {
		return state, errors.New("invalid rollback state")
	}
	return state, nil
}
func extractPackage(path, target string) error {
	archive, err := zip.OpenReader(path)
	if err != nil {
		return err
	}
	defer archive.Close()
	if len(archive.File) == 0 || len(archive.File) > 30000 {
		return errors.New("invalid archive entry count")
	}
	var total uint64
	seen := map[string]bool{}
	for _, file := range archive.File {
		name := file.Name
		clean := filepath.Clean(filepath.FromSlash(name))
		if name == "" || strings.ContainsAny(name, `\:`) || strings.HasPrefix(name, "/") || clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(os.PathSeparator)) || strings.ContainsRune(name, 0) || (!file.Mode().IsRegular() && !file.FileInfo().IsDir()) {
			return fmt.Errorf("unsafe archive path: %q", name)
		}
		for _, part := range strings.Split(strings.TrimSuffix(name, "/"), "/") {
			upper := strings.ToUpper(strings.SplitN(part, ".", 2)[0])
			if part == "" || part == "." || part == ".." || strings.HasSuffix(part, ".") || strings.HasSuffix(part, " ") || reservedPath.MatchString(upper) {
				return errors.New("ambiguous archive path")
			}
		}
		canonical := strings.ToLower(clean)
		if seen[canonical] {
			return errors.New("duplicate archive path")
		}
		seen[canonical] = true
		if file.UncompressedSize64 > 2<<30 || total > (2<<30)-file.UncompressedSize64 {
			return errors.New("archive too large")
		}
		total += file.UncompressedSize64
		dest := filepath.Join(target, clean)
		if file.FileInfo().IsDir() {
			if err = os.MkdirAll(dest, 0755); err != nil {
				return err
			}
			continue
		}
		if err = os.MkdirAll(filepath.Dir(dest), 0755); err != nil {
			return err
		}
		src, e := file.Open()
		if e != nil {
			return e
		}
		mode := os.FileMode(0644)
		if file.Mode().Perm()&0111 != 0 {
			mode = 0755
		}
		dst, e := os.OpenFile(dest, os.O_CREATE|os.O_EXCL|os.O_WRONLY, mode)
		if e != nil {
			src.Close()
			return e
		}
		n, e := io.Copy(dst, io.LimitReader(src, int64(file.UncompressedSize64)+1))
		src.Close()
		if e == nil {
			e = dst.Sync()
		}
		ce := dst.Close()
		if e != nil {
			return e
		}
		if ce != nil {
			return ce
		}
		if n != int64(file.UncompressedSize64) {
			return errors.New("archive size mismatch")
		}
	}
	return nil
}
func copyConfig(src, dst string) error {
	return filepath.WalkDir(src, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if entry.IsDir() {
			return os.MkdirAll(target, 0700)
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("configuration contains unsupported file: %s", rel)
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		defer input.Close()
		output, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		_, err = io.Copy(output, input)
		if err == nil {
			err = output.Sync()
		}
		ce := output.Close()
		if err != nil {
			return err
		}
		return ce
	})
}
func applyPending(root string, state activeState) (activeState, error) {
	data, err := os.ReadFile(filepath.Join(root, "updates", "pending.json"))
	if err != nil {
		return state, err
	}
	key, err := os.ReadFile(filepath.Join(root, "release-public-key.pem"))
	if err != nil {
		return state, err
	}
	rel, err := verifyRelease(data, key)
	if err != nil {
		return state, err
	}
	if !newer(rel.Version, state.Version) {
		return state, errors.New("update is not newer than current version")
	}
	archive := filepath.Join(root, "updates", rel.SHA256+".zip")
	f, err := os.Open(archive)
	if err != nil {
		return state, err
	}
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(f, rel.Size+1))
	f.Close()
	if err != nil {
		return state, err
	}
	if size != rel.Size || hex.EncodeToString(hash.Sum(nil)) != rel.SHA256 {
		return state, errors.New("update package hash mismatch")
	}
	versions := filepath.Join(root, "versions")
	temp, err := os.MkdirTemp(versions, ".update-")
	if err != nil {
		return state, err
	}
	defer os.RemoveAll(temp)
	if err = extractPackage(archive, temp); err != nil {
		return state, err
	}
	var packed struct {
		Version string `json:"version"`
	}
	meta, err := os.ReadFile(filepath.Join(temp, "resources", "app", "package.json"))
	if err != nil {
		return state, err
	}
	if json.Unmarshal(meta, &packed) != nil || packed.Version != rel.Version {
		return state, errors.New("packaged version differs from signed manifest")
	}
	if _, err = os.Stat(filepath.Join(temp, appExecutable())); err != nil {
		return state, err
	}
	next := filepath.Join(versions, rel.Version)
	backup := "config-" + rel.Version
	backupPath := filepath.Join(root, "updates", backup)
	if err = os.MkdirAll(filepath.Join(root, "config"), 0700); err != nil {
		return state, err
	}
	if err = os.RemoveAll(backupPath); err != nil {
		return state, err
	}
	if err = copyConfig(filepath.Join(root, "config"), backupPath); err != nil {
		return state, err
	}
	// Only a version newer than the active version can reach this path.
	if err = os.RemoveAll(next); err != nil {
		return state, err
	}
	if err = os.Rename(temp, next); err != nil {
		return state, err
	}
	updated := activeState{Version: rel.Version, Previous: state.Version, Pending: true, Backup: backup}
	if err = writeJSON(filepath.Join(root, "active.json"), updated); err != nil {
		return state, err
	}
	if err = os.Remove(filepath.Join(root, "updates", "pending.json")); err != nil {
		return updated, err
	}
	return updated, nil
}
func rollback(root string, state activeState) (activeState, error) {
	if !state.Pending {
		return state, nil
	}
	backup := filepath.Join(root, "updates", state.Backup)
	config := filepath.Join(root, "config")
	failed := filepath.Join(root, "updates", "failed-"+state.Version)
	// Record the rollback before moving directories; the next launch can finish it.
	marker := filepath.Join(root, "updates", "rollback.json")
	if err := writeJSON(marker, state); err != nil {
		return state, err
	}
	restore := filepath.Join(root, "updates", "restore-"+state.Version)
	if err := os.RemoveAll(restore); err != nil {
		return state, err
	}
	if err := copyConfig(backup, restore); err != nil {
		return state, err
	}
	if _, err := os.Stat(failed); os.IsNotExist(err) {
		if err = os.Rename(config, failed); err != nil && !os.IsNotExist(err) {
			return state, err
		}
	} else if err != nil {
		return state, err
	} else if err = os.RemoveAll(config); err != nil {
		return state, err
	}
	if err := os.Rename(restore, config); err != nil {
		return state, err
	}
	previous := activeState{Version: state.Previous}
	if err := writeJSON(filepath.Join(root, "active.json"), previous); err != nil {
		return state, err
	}
	if err := os.Remove(marker); err != nil {
		return previous, err
	}
	return previous, nil
}
