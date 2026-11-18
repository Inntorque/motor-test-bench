//go:build windows

package main

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

func lockBusy(err error) bool { return errors.Is(err, syscall.Errno(33)) }

var lockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")
var messageBox = syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")

func lockRoot(root string) (*os.File, error) {
	f, e := os.OpenFile(filepath.Join(root, ".launcher.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	var overlapped syscall.Overlapped
	ok, _, err := lockFileEx.Call(f.Fd(), 3, 0, 1, 0, uintptr(unsafe.Pointer(&overlapped)))
	if ok == 0 {
		f.Close()
		return nil, err
	}
	return f, nil
}
func showError(message string) {
	m, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString("Inntorque 测功机")
	messageBox.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(title)), 0x10)
}
