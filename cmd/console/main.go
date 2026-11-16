// Saferus demo exercises a fixed synthetic operation. Vault administration and
// arbitrary secret material stay in the OpenBao API/UI, outside this demo.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/os-santiago/saferus/web"
)

type vault struct {
	client           *http.Client
	address, jwtFile string
}

func (v vault) call(ctx context.Context, path string, body any, token string) (map[string]any, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return nil, errors.New("encode request")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, v.address+"/v1/"+path, bytes.NewReader(raw))
	if err != nil {
		return nil, errors.New("create request")
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("X-Vault-Token", token)
	}
	resp, err := v.client.Do(req)
	if err != nil {
		return nil, errors.New("vault unavailable")
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, 1048577))
	if err != nil || len(b) > 1048576 || (resp.StatusCode != 200 && resp.StatusCode != 204) {
		return nil, errors.New("vault rejected request")
	}
	if resp.StatusCode == 204 {
		return map[string]any{}, nil
	}
	var result map[string]any
	if json.Unmarshal(b, &result) != nil {
		return nil, errors.New("vault response invalid")
	}
	return result, nil
}

func object(m map[string]any, key string) map[string]any { v, _ := m[key].(map[string]any); return v }
func text(m map[string]any, key string) string           { v, _ := m[key].(string); return v }

func (v vault) demonstrate(ctx context.Context) (map[string]any, error) {
	jwt, err := os.ReadFile(v.jwtFile)
	if err != nil || len(jwt) == 0 || len(jwt) > 16384 {
		return nil, errors.New("workload identity unavailable")
	}
	login, err := v.call(ctx, "auth/kubernetes/login", map[string]string{"role": "demo", "jwt": string(jwt)}, "")
	if err != nil {
		return nil, err
	}
	auth := object(login, "auth")
	token := text(auth, "client_token")
	ttl, _ := auth["lease_duration"].(float64)
	if token == "" {
		return nil, errors.New("missing workload token")
	}
	revoked := false
	defer func() {
		if !revoked {
			cleanup, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			_, _ = v.call(cleanup, "auth/token/revoke-self", map[string]string{}, token)
		}
	}()
	if ttl <= 0 || ttl > 60 {
		return nil, errors.New("unexpected token lifetime")
	}
	plain := base64.StdEncoding.EncodeToString([]byte("Saferus synthetic demonstration"))
	encrypted, err := v.call(ctx, "transit/encrypt/demo", map[string]string{"plaintext": plain}, token)
	if err != nil {
		return nil, err
	}
	ciphertext := text(object(encrypted, "data"), "ciphertext")
	if ciphertext == "" {
		return nil, errors.New("missing ciphertext")
	}
	decrypted, err := v.call(ctx, "transit/decrypt/demo", map[string]string{"ciphertext": ciphertext}, token)
	if err != nil || text(object(decrypted, "data"), "plaintext") != plain {
		return nil, errors.New("roundtrip failed")
	}
	if _, err = v.call(ctx, "auth/token/revoke-self", map[string]string{}, token); err != nil {
		return nil, errors.New("revocation not confirmed")
	}
	revoked = true
	return map[string]any{"verified": true, "algorithm": "AES-256-GCM", "tokenTTLSeconds": int(ttl), "tokenRevoked": true, "agentless": true}, nil
}

func handler(v vault) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("GET /", http.FileServer(http.FS(web.Files)))
	mux.HandleFunc("GET /health/live", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusNoContent) })
	// Bound cryptographic work. This local-only synthetic demo is not a public
	// unauthenticated encryption service and accepts no user secret/plaintext.
	busy := make(chan struct{}, 1)
	mux.HandleFunc("POST /api/demo", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Origin") != "https://"+r.Host || r.Header.Get("Content-Type") != "application/json" {
			http.Error(w, "request denied", http.StatusForbidden)
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 16))
		if err != nil || !bytes.Equal(bytes.TrimSpace(body), []byte("{}")) {
			http.Error(w, "empty JSON object required", http.StatusBadRequest)
			return
		}
		select {
		case busy <- struct{}{}:
			defer func() { <-busy }()
		default:
			http.Error(w, "demo busy", http.StatusTooManyRequests)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		result, err := v.demonstrate(ctx)
		if err != nil {
			http.Error(w, "Workload verification unavailable", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(result)
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Cache-Control", "no-store")
		mux.ServeHTTP(w, r)
	})
}

func main() {
	address := os.Getenv("SAFERUS_VAULT_URL")
	u, err := url.Parse(address)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		log.Fatal("Invalid trusted vault URL")
	}
	ca, err := os.ReadFile(os.Getenv("SAFERUS_CA_FILE"))
	if err != nil {
		log.Fatal("CA unavailable")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		log.Fatal("Invalid CA")
	}
	client := &http.Client{Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: pool}, MaxIdleConns: 2, MaxConnsPerHost: 2, IdleConnTimeout: 15 * time.Second}}
	v := vault{client: client, address: address, jwtFile: os.Getenv("SAFERUS_JWT_FILE")}
	if len(os.Args) == 2 && (os.Args[1] == "--probe" || os.Args[1] == "--probe-denied") {
		for range 3 {
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			req, _ := http.NewRequestWithContext(ctx, http.MethodGet, address+"/v1/sys/health", nil)
			resp, err := client.Do(req)
			cancel()
			if os.Args[1] == "--probe" {
				if err != nil || resp.StatusCode != 200 {
					log.Fatal("Allowed network probe failed")
				}
				resp.Body.Close()
			} else {
				if resp != nil {
					resp.Body.Close()
				}
				var timeout net.Error
				if err == nil || !errors.As(err, &timeout) || !timeout.Timeout() {
					log.Fatal("Denied network probe did not time out")
				}
			}
		}
		log.Print("Network probe passed")
		return
	}
	s := &http.Server{Addr: ":8443", Handler: handler(v), ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 12 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS13}}
	if s.ListenAndServeTLS(os.Getenv("SAFERUS_TLS_CERT"), os.Getenv("SAFERUS_TLS_KEY")) != nil {
		log.Fatal("HTTPS server stopped")
	}
}
