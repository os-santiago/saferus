// Package web embeds the local demonstration UI into the standalone console.
package web

import "embed"

//go:embed index.html static/*
var Files embed.FS
