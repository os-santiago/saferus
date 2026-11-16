package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDemonstrationUsesFixedPayloadAndRevokes(t *testing.T) {
	for _, mode := range []string{"success", "wrong-plaintext", "excessive-ttl", "failed-revoke", "failed-login"} {
		t.Run(mode, func(t *testing.T) {
			calls := []string{}
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls = append(calls, r.URL.Path)
				var body map[string]string
				if json.NewDecoder(r.Body).Decode(&body) != nil {
					t.Error("invalid body")
				}
				w.Header().Set("Content-Type", "application/json")
				switch r.URL.Path {
				case "/v1/auth/kubernetes/login":
					if body["role"] != "demo" || body["jwt"] != "fixture-jwt" || r.Header.Get("X-Vault-Token") != "" {
						t.Error("wrong workload login")
					}
					if mode == "failed-login" {
						w.WriteHeader(403)
						return
					}
					ttl := 60
					if mode == "excessive-ttl" {
						ttl = 61
					}
					_ = json.NewEncoder(w).Encode(map[string]any{"auth": map[string]any{"client_token": "fixture-sensitive-token", "lease_duration": ttl}})
				case "/v1/transit/encrypt/demo":
					if body["plaintext"] != "U2FmZXJ1cyBzeW50aGV0aWMgZGVtb25zdHJhdGlvbg==" {
						t.Error("non-synthetic plaintext")
					}
					_, _ = w.Write([]byte(`{"data":{"ciphertext":"vault:v1:fixture"}}`))
				case "/v1/transit/decrypt/demo":
					if body["ciphertext"] != "vault:v1:fixture" {
						t.Error("different cipher")
					}
					plain := "U2FmZXJ1cyBzeW50aGV0aWMgZGVtb25zdHJhdGlvbg=="
					if mode == "wrong-plaintext" {
						plain = "wrong"
					}
					_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]string{"plaintext": plain}})
				case "/v1/auth/token/revoke-self":
					if mode == "failed-revoke" {
						w.WriteHeader(503)
					} else {
						w.WriteHeader(204)
					}
				default:
					t.Error("unexpected API call")
					w.WriteHeader(404)
				}
				if r.URL.Path != "/v1/auth/kubernetes/login" && r.Header.Get("X-Vault-Token") != "fixture-sensitive-token" {
					t.Error("missing scoped token")
				}
			}))
			defer server.Close()
			jwt := filepath.Join(t.TempDir(), "token")
			if os.WriteFile(jwt, []byte("fixture-jwt"), 0600) != nil {
				t.Fatal("fixture write")
			}
			v := vault{client: server.Client(), address: server.URL, jwtFile: jwt}
			result, err := v.demonstrate(context.Background())
			if (err == nil) != (mode == "success") {
				t.Fatal("incorrect result", mode, err)
			}
			encoded, _ := json.Marshal(result)
			for _, forbidden := range []string{"fixture-sensitive-token", "fixture-jwt", "vault:v1:fixture"} {
				if strings.Contains(string(encoded), forbidden) {
					t.Fatal("secret leaked in demo result")
				}
			}
			if mode != "failed-login" && calls[len(calls)-1] != "/v1/auth/token/revoke-self" {
				t.Fatal("missing cleanup")
			}
		})
	}
}

func TestBrowserBoundaryAndSecurityHeaders(t *testing.T) {
	h := handler(vault{})
	for _, tc := range []struct {
		origin, content, body string
		code                  int
	}{
		{"https://untrusted.example", "application/json", "{}", 403},
		{"", "application/json", "{}", 403},
		{"https://console.example", "text/plain", "{}", 403},
		{"https://console.example", "application/json", `{"secret":"no"}`, 400},
		{"https://console.example", "application/json", strings.Repeat("a", 17), 400},
	} {
		r := httptest.NewRequest("POST", "https://console.example/api/demo", strings.NewReader(tc.body))
		r.Header.Set("Origin", tc.origin)
		r.Header.Set("Content-Type", tc.content)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.code {
			t.Fatal(w.Code, tc.code)
		}
		if w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("X-Content-Type-Options") != "nosniff" {
			t.Fatal("missing security headers")
		}
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "https://console.example/", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), "Run verification") {
		t.Fatal("console not served")
	}
}
