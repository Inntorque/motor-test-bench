package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"
)

func appExecutable() string {
	if runtime.GOOS == "windows" {
		return "InntorqueBench.exe"
	}
	return "inntorque-bench"
}
func launch(root string, state activeState) (bool, error) {
	path := filepath.Join(root, "versions", state.Version, appExecutable())
	tokenBytes := make([]byte, 24)
	if _, err := rand.Read(tokenBytes); err != nil {
		return false, err
	}
	token := hex.EncodeToString(tokenBytes)
	health := filepath.Join(root, "updates", "health-"+token+".json")
	defer os.Remove(health)
	command := exec.Command(path, os.Args[1:]...)
	command.Dir = filepath.Dir(path)
	command.Env = append(os.Environ(), "INNTORQUE_APP_ROOT="+root)
	command.Env = append(command.Env, "INNTORQUE_BOOT_TOKEN="+token)
	log, err := os.OpenFile(filepath.Join(root, "logs", "application.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return false, err
	}
	defer log.Close()
	command.Stdout = log
	command.Stderr = log
	if err = command.Start(); err != nil {
		return false, err
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	timeout := time.NewTimer(45 * time.Second)
	defer timeout.Stop()
	tick := time.NewTicker(100 * time.Millisecond)
	defer tick.Stop()
	ready := false
	for {
		select {
		case err = <-done:
			if !ready && err == nil {
				err = errors.New("application exited before startup confirmation")
			}
			return ready, err
		case <-timeout.C:
			if !ready {
				_ = command.Process.Kill()
				<-done
				return false, errors.New("application did not become ready within 45 seconds")
			}
		case <-tick.C:
			if ready {
				continue
			}
			data, e := os.ReadFile(health)
			var ack struct {
				Token   string `json:"token"`
				Version string `json:"version"`
			}
			if e == nil && json.Unmarshal(data, &ack) == nil && ack.Token == token && ack.Version == state.Version {
				ready = true
				timeout.Stop()
				if state.Pending {
					state.Pending = false
					if e = writeJSON(filepath.Join(root, "active.json"), state); e != nil {
						_ = command.Process.Kill()
						<-done
						return false, e
					}
				}
			}
		}
	}
}
func run(root string) error {
	for _, name := range []string{"updates", "logs", "versions", "config", "测功数据"} {
		if err := os.MkdirAll(filepath.Join(root, name), 0700); err != nil {
			return err
		}
	}
	lock, err := lockRoot(root)
	if err != nil {
		if !lockBusy(err) {
			return err
		}
		return nil
	}
	defer lock.Close()
	state, err := readActive(root)
	if err != nil {
		return err
	}
	if data, e := os.ReadFile(filepath.Join(root, "updates", "rollback.json")); e == nil {
		var interrupted activeState
		if json.Unmarshal(data, &interrupted) != nil || !interrupted.Pending || !versionPattern.MatchString(interrupted.Version) || !versionPattern.MatchString(interrupted.Previous) || interrupted.Backup != "config-"+interrupted.Version {
			return errors.New("invalid rollback journal")
		}
		state, err = rollback(root, interrupted)
		if err != nil {
			return err
		}
	} else if !os.IsNotExist(e) {
		return e
	} else if state.Pending {
		state, err = rollback(root, state)
		if err != nil {
			return err
		}
	}
	if _, e := os.Stat(filepath.Join(root, "updates", "pending.json")); e == nil {
		state, err = applyPending(root, state)
		if err != nil {
			recordError(root, err)
			_ = replaceFile(filepath.Join(root, "updates", "pending.json"), filepath.Join(root, "updates", "rejected.json"))
			if state.Pending {
				state, err = rollback(root, state)
				if err != nil {
					return err
				}
			}
		}
	} else if !os.IsNotExist(e) {
		return e
	}
	for {
		ready, launchErr := launch(root, state)
		if !ready && state.Pending {
			recordError(root, fmt.Errorf("new version failed, rolling back: %w", launchErr))
			state, err = rollback(root, state)
			if err != nil {
				return err
			}
			continue
		}
		if !ready {
			return launchErr
		}
		state, err = readActive(root)
		if err != nil {
			return err
		}
		if _, err = os.Stat(filepath.Join(root, "updates", "pending.json")); os.IsNotExist(err) {
			return launchErr
		}
		state, err = applyPending(root, state)
		if err != nil {
			recordError(root, err)
			_ = replaceFile(filepath.Join(root, "updates", "pending.json"), filepath.Join(root, "updates", "rejected.json"))
			if state.Pending {
				state, err = rollback(root, state)
				if err != nil {
					return err
				}
			}
			_, err = launch(root, state)
			return err
		}
	}
}
func recordError(root string, err error) {
	_ = os.MkdirAll(filepath.Join(root, "logs"), 0700)
	file, e := os.OpenFile(filepath.Join(root, "logs", "launcher.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if e == nil {
		fmt.Fprintf(file, "%s %v\n", time.Now().Format(time.RFC3339), err)
		file.Close()
	}
	_ = writeJSON(filepath.Join(root, "updates", "last-error.json"), map[string]string{"error": err.Error(), "time": time.Now().Format(time.RFC3339)})
}
func main() {
	exe, err := os.Executable()
	if err != nil {
		os.Exit(1)
	}
	root := filepath.Dir(exe)
	if err = run(root); err != nil {
		recordError(root, err)
		showError("测功机启动或更新失败。请查看软件目录 logs/launcher.log。\n" + err.Error())
		os.Exit(1)
	}
}
