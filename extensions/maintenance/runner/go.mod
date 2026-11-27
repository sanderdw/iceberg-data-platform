module github.com/sanderdw/iceberg-data-platform/extensions/maintenance/runner

go 1.26.3

// iceberg-go is pinned to a main commit (e7a4d96, 2026-11-18): dangling-delete removal and other
// maintenance actions this runner uses are not in a release yet (see DECISIONS.md).
require (
	github.com/apache/arrow-go/v18 v18.9.0
	github.com/apache/iceberg-go v0.7.1-0.20261118175253-e7a4d96b505d
)

require (
	cloud.google.com/go v0.123.0 // indirect
	github.com/RoaringBitmap/roaring/v2 v2.30.0 // indirect
	github.com/andybalholm/brotli v1.3.0 // indirect
	github.com/antlr4-go/antlr/v4 v4.13.1 // indirect
	github.com/apache/thrift v0.25.0 // indirect
	github.com/aws/aws-sdk-go-v2 v1.48.0 // indirect
	github.com/aws/aws-sdk-go-v2/aws/protocol/eventstream v1.7.27 // indirect
	github.com/aws/aws-sdk-go-v2/config v1.33.21 // indirect
	github.com/aws/aws-sdk-go-v2/credentials v1.21.3 // indirect
	github.com/aws/aws-sdk-go-v2/feature/ec2/imds v1.20.9 // indirect
	github.com/aws/aws-sdk-go-v2/feature/s3/transfermanager v0.5.12 // indirect
	github.com/aws/aws-sdk-go-v2/internal/configsources v1.5.12 // indirect
	github.com/aws/aws-sdk-go-v2/internal/endpoints/v2 v2.8.12 // indirect
	github.com/aws/aws-sdk-go-v2/internal/v4a v1.5.12 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/accept-encoding v1.13.25 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/checksum v1.11.13 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/presigned-url v1.14.12 // indirect
	github.com/aws/aws-sdk-go-v2/service/internal/s3shared v1.20.12 // indirect
	github.com/aws/aws-sdk-go-v2/service/s3 v1.116.0 // indirect
	github.com/aws/aws-sdk-go-v2/service/signin v1.14.0 // indirect
	github.com/aws/aws-sdk-go-v2/service/sso v1.39.5 // indirect
	github.com/aws/aws-sdk-go-v2/service/ssooidc v1.44.5 // indirect
	github.com/aws/aws-sdk-go-v2/service/sts v1.53.0 // indirect
	github.com/aws/smithy-go v1.28.10 // indirect
	github.com/bits-and-blooms/bitset v1.30.0 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/cockroachdb/apd/v3 v3.2.3 // indirect
	github.com/creasty/defaults v1.11.0 // indirect
	github.com/geoarrow/geoarrow-go v0.0.0-20261120233755-d51ce21f1d56 // indirect
	github.com/go-logr/logr v1.4.4 // indirect
	github.com/go-logr/stdr v1.2.2 // indirect
	github.com/goccy/go-json v0.12.0 // indirect
	github.com/goccy/go-yaml v1.19.2 // indirect
	github.com/google/flatbuffers v25.12.19+incompatible // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/google/wire v0.7.0 // indirect
	github.com/googleapis/gax-go/v2 v2.27.0 // indirect
	github.com/hashicorp/golang-lru/v2 v2.0.7 // indirect
	github.com/klauspost/compress v1.20.5 // indirect
	github.com/klauspost/cpuid/v2 v2.4.2 // indirect
	github.com/kr/text v0.2.0 // indirect
	github.com/mschoch/smat v0.2.0 // indirect
	github.com/pierrec/lz4/v4 v4.1.34 // indirect
	github.com/substrait-io/substrait v0.109.0 // indirect
	github.com/substrait-io/substrait-go/v8 v8.1.1 // indirect
	github.com/substrait-io/substrait-protobuf/go v0.85.0 // indirect
	github.com/twmb/avro v1.9.1 // indirect
	github.com/twmb/murmur3 v1.2.2 // indirect
	github.com/twpayne/go-geom v1.7.0 // indirect
	github.com/zeebo/xxh3 v1.1.0 // indirect
	go.opentelemetry.io/auto/sdk v1.2.2 // indirect
	go.opentelemetry.io/otel v1.47.0 // indirect
	go.opentelemetry.io/otel/log v1.47.0 // indirect
	go.opentelemetry.io/otel/metric v1.47.0 // indirect
	go.opentelemetry.io/otel/sdk v1.47.0 // indirect
	go.opentelemetry.io/otel/sdk/metric v1.47.0 // indirect
	go.opentelemetry.io/otel/trace v1.47.0 // indirect
	gocloud.dev v0.49.0 // indirect
	golang.org/x/exp v0.0.0-20261124000051-d6646c509b6d // indirect
	golang.org/x/net v0.63.0 // indirect
	golang.org/x/oauth2 v0.38.0 // indirect
	golang.org/x/sync v0.25.0 // indirect
	golang.org/x/sys v0.50.0 // indirect
	golang.org/x/text v0.44.0 // indirect
	golang.org/x/xerrors v0.0.0-20240903120638-7835f813f4da // indirect
	google.golang.org/api v0.317.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20261123191846-1e707ab9fbc2 // indirect
	google.golang.org/grpc v1.85.0 // indirect
	google.golang.org/protobuf v1.36.12 // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)
