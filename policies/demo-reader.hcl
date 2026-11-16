path "secret/data/demo/service" {
  capabilities = ["read"]
}
path "transit/encrypt/demo" {
  capabilities = ["update"]
}
path "transit/decrypt/demo" {
  capabilities = ["update"]
}
path "auth/token/revoke-self" {
  capabilities = ["update"]
}
