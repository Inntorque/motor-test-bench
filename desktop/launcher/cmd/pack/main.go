package main

import (
	"archive/zip"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

func pack(source, destination string) error {
	out, e := os.Create(destination)
	if e != nil {
		return e
	}
	defer out.Close()
	z := zip.NewWriter(out)
	e = filepath.WalkDir(source, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("non-regular package file: %s", path)
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		header, err := zip.FileInfoHeader(info)
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(relative)
		header.Method = zip.Deflate
		dest, err := z.CreateHeader(header)
		if err != nil {
			return err
		}
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		defer file.Close()
		_, err = io.Copy(dest, file)
		return err
	})
	if e != nil {
		z.Close()
		return e
	}
	if e = z.Close(); e != nil {
		return e
	}
	return out.Sync()
}
func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "pack SOURCE DESTINATION.zip")
		os.Exit(2)
	}
	if err := pack(os.Args[1], os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
