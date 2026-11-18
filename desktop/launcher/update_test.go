package main

import (
	"archive/zip"
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func signedTest(t *testing.T, r release) ([]byte, []byte) {
	t.Helper()
	pub, priv, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	payload, _ := json.Marshal(r)
	data, _ := json.Marshal(envelope{base64.StdEncoding.EncodeToString(payload), base64.StdEncoding.EncodeToString(ed25519.Sign(priv, payload))})
	der, _ := x509.MarshalPKIXPublicKey(pub)
	return data, pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der})
}
func fixtureRelease() release {
	p := runtime.GOOS
	if p == "windows" {
		p = "win32"
	}
	return release{Schema: 1, Version: "1.2.0", Platform: p, Arch: "x64", MinLauncher: 1, ProtocolMajor: 1, DataSchema: 3, Size: 12, SHA256: hex.EncodeToString(make([]byte, 32))}
}
func zipTest(t *testing.T, files map[string]string) []byte {
	t.Helper()
	var out bytes.Buffer
	z := zip.NewWriter(&out)
	for name, data := range files {
		h := &zip.FileHeader{Name: name, Method: zip.Deflate}
		h.SetMode(0755)
		f, e := z.CreateHeader(h)
		if e != nil {
			t.Fatal(e)
		}
		f.Write([]byte(data))
	}
	if e := z.Close(); e != nil {
		t.Fatal(e)
	}
	return out.Bytes()
}
func TestSignatureAndCompatibility(t *testing.T) {
	r := fixtureRelease()
	data, key := signedTest(t, r)
	if _, e := verifyRelease(data, key); e != nil {
		t.Fatal(e)
	}
	for _, change := range []func(*release){func(r *release) { r.Platform = "bad" }, func(r *release) { r.Version = "../escape" }, func(r *release) { r.MinLauncher = 2 }, func(r *release) { r.ProtocolMajor = 2 }, func(r *release) { r.DataSchema = 9 }, func(r *release) { r.Size = -1 }} {
		r = fixtureRelease()
		change(&r)
		data, key = signedTest(t, r)
		if _, e := verifyRelease(data, key); e == nil {
			t.Fatal("accepted incompatible release")
		}
	}
	data, key = signedTest(t, fixtureRelease())
	data[len(data)-6] ^= 1
	if _, e := verifyRelease(data, key); e == nil {
		t.Fatal("accepted tampering")
	}
}
func TestRejectArchiveTraversalAndCollisions(t *testing.T) {
	for _, files := range []map[string]string{{"../outside": "bad"}, {"/absolute": "bad"}, {"a\\b": "bad"}, {"NUL.txt": "bad"}, {"a": "1", "A": "2"}, {"a/../b": "bad"}, {"a.": "bad"}} {
		root := t.TempDir()
		archive := filepath.Join(root, "bad.zip")
		os.WriteFile(archive, zipTest(t, files), 0600)
		if e := extractPackage(archive, filepath.Join(root, "out")); e == nil {
			t.Fatalf("accepted %v", files)
		}
	}
}
func updateFixture(t *testing.T) (string, activeState) {
	t.Helper()
	root := t.TempDir()
	for _, name := range []string{"versions/1.1.0", "updates", "config", "测功数据"} {
		os.MkdirAll(filepath.Join(root, name), 0700)
	}
	os.WriteFile(filepath.Join(root, "config/settings.json"), []byte("old config"), 0600)
	os.WriteFile(filepath.Join(root, "测功数据/raw.csv"), []byte("real measurements"), 0600)
	bundle := zipTest(t, map[string]string{appExecutable(): "fixture", "resources/app/package.json": `{"version":"1.2.0"}`})
	hash := sha256.Sum256(bundle)
	r := fixtureRelease()
	r.SHA256 = hex.EncodeToString(hash[:])
	r.Size = int64(len(bundle))
	data, key := signedTest(t, r)
	os.WriteFile(filepath.Join(root, "release-public-key.pem"), key, 0600)
	os.WriteFile(filepath.Join(root, "updates/pending.json"), data, 0600)
	os.WriteFile(filepath.Join(root, "updates", r.SHA256+".zip"), bundle, 0600)
	state := activeState{Version: "1.1.0"}
	writeJSON(filepath.Join(root, "active.json"), state)
	return root, state
}
func TestApplyAndRollbackPreserveMeasurementsAndRestoreConfig(t *testing.T) {
	root, old := updateFixture(t)
	next, e := applyPending(root, old)
	if e != nil {
		t.Fatal(e)
	}
	if next.Version != "1.2.0" || !next.Pending || next.Previous != old.Version {
		t.Fatal(next)
	}
	os.WriteFile(filepath.Join(root, "config/settings.json"), []byte("migrated config"), 0600)
	restored, e := rollback(root, next)
	if e != nil {
		t.Fatal(e)
	}
	if restored.Version != old.Version || restored.Pending {
		t.Fatal(restored)
	}
	data, _ := os.ReadFile(filepath.Join(root, "config/settings.json"))
	if string(data) != "old config" {
		t.Fatal(string(data))
	}
	data, _ = os.ReadFile(filepath.Join(root, "测功数据/raw.csv"))
	if string(data) != "real measurements" {
		t.Fatal(string(data))
	}
	data, _ = os.ReadFile(filepath.Join(root, "updates/failed-1.2.0/settings.json"))
	if string(data) != "migrated config" {
		t.Fatal(string(data))
	}
}
func TestBadPackageDoesNotChangeActiveVersion(t *testing.T) {
	root, state := updateFixture(t)
	files, _ := filepath.Glob(filepath.Join(root, "updates/*.zip"))
	os.WriteFile(files[0], []byte("truncated"), 0600)
	if _, e := applyPending(root, state); e == nil {
		t.Fatal("accepted bad package")
	}
	actual, _ := readActive(root)
	if actual != state {
		t.Fatal(actual)
	}
}
func TestRetryInterruptedExtraction(t *testing.T) {
	root, state := updateFixture(t)
	os.MkdirAll(filepath.Join(root, "versions/1.2.0"), 0700)
	os.WriteFile(filepath.Join(root, "versions/1.2.0/incomplete"), []byte("partial"), 0600)
	if _, e := applyPending(root, state); e != nil {
		t.Fatal(e)
	}
	if _, e := os.Stat(filepath.Join(root, "versions/1.2.0/incomplete")); !os.IsNotExist(e) {
		t.Fatal("orphan not replaced")
	}
}
func TestRollbackJournalResumesAfterConfigMove(t *testing.T) {
	root, state := updateFixture(t)
	next, e := applyPending(root, state)
	if e != nil {
		t.Fatal(e)
	}
	writeJSON(filepath.Join(root, "updates/rollback.json"), next)
	os.Rename(filepath.Join(root, "config"), filepath.Join(root, "updates/failed-1.2.0"))
	if _, e = rollback(root, next); e != nil {
		t.Fatal(e)
	}
	data, _ := os.ReadFile(filepath.Join(root, "config/settings.json"))
	if string(data) != "old config" {
		t.Fatal(string(data))
	}
}
