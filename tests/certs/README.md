# 시험 전용 인증서 (127.0.0.1)

`tests/fake_backend.py`가 HTTPS를 열 때 쓴다. 실제 서버에 쓰지 않는다. 키도 시험용이라 공개돼도 상관없다.

| 파일 | 내용 |
|---|---|
| `server.pem` · `server.key` | 시험 CA(`ca.pem`)가 서명한 인증서. SAN `IP:127.0.0.1`, `DNS:localhost`. 시스템 인증서 저장소 경로 시험(`SSL_CERT_FILE=ca.pem`)과 미신뢰 인증서 시험에 쓴다 |
| `other.pem` · `other.key` | 자체 서명 인증서 (팀 서버 Caddy와 같은 방식). 지문 고정과 인증서 교체(지문 불일치) 시험에 쓴다 |
| `ca.pem` | 시험 CA 인증서. CA 키는 만들고 바로 버렸으므로 이 CA로 다른 인증서를 만들 수 없다 |

다시 만들 때 (EC P-256, 100년):

```bash
openssl ecparam -name prime256v1 -genkey -noout -out ca.key
openssl req -x509 -new -key ca.key -sha256 -days 36500 -subj "/CN=KP Guard test CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" -out ca.pem
openssl ecparam -name prime256v1 -genkey -noout -out server.key
openssl req -new -key server.key -subj "/CN=127.0.0.1" -out server.csr
printf "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1,DNS:localhost\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n" > server.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 36500 -sha256 -extfile server.ext -out server.pem
openssl ecparam -name prime256v1 -genkey -noout -out other.key
openssl req -x509 -new -key other.key -sha256 -days 36500 -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1" -out other.pem
rm ca.key ca.srl server.csr server.ext
```
