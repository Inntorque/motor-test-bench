//go:build !windows

package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

func lockBusy(err error) bool { return errors.Is(err, syscall.EWOULDBLOCK) }

func lockRoot(root string) (*os.File, error) {
	f, e := os.OpenFile(filepath.Join(root, ".launcher.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		f.Close()
		return nil, e
	}
	return f, nil
}
func showError(message string) { fmt.Fprintln(os.Stderr, message) }
