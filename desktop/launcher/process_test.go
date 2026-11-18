package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestMain(m *testing.M) {
	if mode := os.Getenv("INNTORQUE_LAUNCHER_TEST_CHILD"); mode != "" {
		if mode == "exit" {
			os.Exit(0)
		}
		root := os.Getenv("INNTORQUE_APP_ROOT")
		exe, _ := os.Executable()
		token := os.Getenv("INNTORQUE_BOOT_TOKEN")
		data, _ := json.Marshal(map[string]string{"token": token, "version": filepath.Base(filepath.Dir(exe))})
		if err := os.WriteFile(filepath.Join(root, "updates", "health-"+token+".json"), data, 0600); err != nil {
			os.Exit(2)
		}
		time.Sleep(350 * time.Millisecond)
		os.Exit(0)
	}
	os.Exit(m.Run())
}
func childFixture(t *testing.T, root, version string) {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(exe)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "versions", version, appExecutable())
	if err = os.WriteFile(path, data, 0755); err != nil {
		t.Fatal(err)
	}
	if err = os.MkdirAll(filepath.Join(root, "logs"), 0700); err != nil {
		t.Fatal(err)
	}
}
func TestLaunchConfirmsAndCommitsNewVersion(t *testing.T) {
	root, old := updateFixture(t)
	next, err := applyPending(root, old)
	if err != nil {
		t.Fatal(err)
	}
	childFixture(t, root, next.Version)
	t.Setenv("INNTORQUE_LAUNCHER_TEST_CHILD", "ready")
	ready, err := launch(root, next)
	if err != nil || !ready {
		t.Fatalf("ready=%v err=%v", ready, err)
	}
	state, err := readActive(root)
	if err != nil || state.Pending || state.Version != next.Version {
		t.Fatalf("%+v %v", state, err)
	}
}
func TestRunRestoresOldProcessAfterNewExecutableFails(t *testing.T) {
	root, old := updateFixture(t)
	childFixture(t, root, old.Version)
	t.Setenv("INNTORQUE_LAUNCHER_TEST_CHILD", "ready")
	if err := run(root); err != nil {
		t.Fatal(err)
	}
	state, err := readActive(root)
	if err != nil || state.Version != old.Version || state.Pending {
		t.Fatalf("%+v %v", state, err)
	}
	if _, err = os.Stat(filepath.Join(root, "updates/last-error.json")); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(root, "测功数据/raw.csv"))
	if string(data) != "real measurements" {
		t.Fatal("measurements modified")
	}
}
func TestEarlyCleanExitIsNotHealthy(t *testing.T) {
	root, state := updateFixture(t)
	childFixture(t, root, state.Version)
	t.Setenv("INNTORQUE_LAUNCHER_TEST_CHILD", "exit")
	ready, err := launch(root, state)
	if ready || err == nil {
		t.Fatalf("ready=%v err=%v", ready, err)
	}
}
func TestSecondLauncherLockIsRecognized(t *testing.T) {
	root := t.TempDir()
	first, err := lockRoot(root)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := lockRoot(root)
	if err == nil {
		second.Close()
		t.Fatal("second lock succeeded")
	}
	if !lockBusy(err) {
		t.Fatal(err)
	}
}
