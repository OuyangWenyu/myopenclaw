# 联系人 (cardamum)

Hermes 通过 [cardamum](https://github.com/pimalaya/cardamum) 管理联系人。cardamum v0.1.0 已预装在 Hermes 镜像中。

使用 **vdir** 本地存储（QQ 邮箱和 DLUT 均不支持 CardDAV 远程同步）。联系人以 **vCard 4.0** 格式存储，每个联系人一个 `.vcf` 文件。

## 自动配置

首次启动时 entrypoint 自动创建：

- 配置文件：`~/.hermes/home/.config/cardamum/config.toml`
- 联系人数据：`~/.hermes/.contacts/`（vdir，每个联系人一个 UUID.vcf 文件）

联系人数据通过 `hermes/scripts/backup.sh` 自动纳入云端备份。

## 常用命令

```bash
# 创建通讯录（首次使用，记下返回的 ADDRESSBOOK-ID）
docker compose exec hermes cardamum addressbooks create "Contacts"

# 列出所有联系人
docker compose exec hermes cardamum cards list <ADDRESSBOOK-ID>

# 查看联系人详情
docker compose exec hermes cardamum cards read <ADDRESSBOOK-ID> <CARD-ID>

# JSON 输出（方便 Hermes 解析）
docker compose exec hermes cardamum cards list --json <ADDRESSBOOK-ID>
```

## 添加联系人

联系人以 vCard 4.0 格式的 `.vcf` 文件存储。注意：

- `.vcf` 文件名用 UUID（`uuidgen`）
- vCard 内 `UID` 必须与文件名 UUID 一致
- 使用 `VERSION:4.0`（不是 3.0）

Hermes 可直接写入 `.vcf` 文件来添加联系人。
