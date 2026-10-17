.PHONY: proto lint breaking hooks up

# 拉起 NATS、控制面、worker、memory、graph、Web。角色不写在命令里。
up:
	./scripts/dev.sh

# 从 proto/ 生成 Go 绑定到 gen/go/（依赖本机 buf + protoc-gen-go + protoc-gen-go-grpc）
proto:
	buf generate

# 契约风格门禁
lint:
	buf lint

# 破坏性变更门禁（对 main 分支）
breaking:
	buf breaking --against '.git#branch=main'

# 启用仓库内 .githooks/（pre-commit 会跑 buf generate）
hooks:
	git config core.hooksPath .githooks
