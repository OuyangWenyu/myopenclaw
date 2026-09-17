# 联系人 (cardamum)

Hermes 通过 [cardamum](https://github.com/pimalaya/cardamum) 管理联系人。cardamum **v0.2.0** 由镜像从源码构建（rev `771879c`）——唯一的二进制发行版 v0.1.0 仍是旧的 `$EDITOR` 式 `cards create` 流程，不可用。

使用 **vdir** 本地存储（QQ 邮箱和 DLUT/Coremail 均不支持 CardDAV 远程同步）。联系人以 **vCard 4.0** 格式存储，每个联系人一个 `.vcf` 文件。

## 自动配置

首次启动时 entrypoint 自动创建：

- 配置文件：`~/.hermes/home/.config/cardamum/config.toml`
- 联系人数据：`~/.hermes/.contacts/`（vdir，联系人以 UUID 命名的 `.vcf` 文件存放在按通讯录 ID 命名的子目录下，每个子目录还有一个 `displayname` 文件）
- 通讯录本身（默认名 `contacts`）：vdir 为空时自动创建，其 ID 被写回配置的 `[addressbook] default`，因此后续 `card` 命令无需再传 `-k/--addressbook`

联系人数据通过 `hermes/scripts/backup.sh` 自动纳入云端备份。

## 常用命令

> 子命令用**单数** `addressbook` / `card`（v0.2.0 没有复数别名）。`card` 命令缺省作用于 `addressbook.default`，只有配置里没有默认值时才需要 `-k <ADDRESSBOOK-ID>`。

```bash
# 创建通讯录（首次使用；entrypoint 已自动完成，通常不需要手动执行）
docker compose exec hermes cardamum addressbook create "contacts"

# 列出所有通讯录
docker compose exec hermes cardamum addressbook list

# 列出所有联系人
docker compose exec hermes cardamum card list

# 查看联系人详情
docker compose exec hermes cardamum card read <CARD-ID>

# JSON 输出（方便 Hermes 解析）
docker compose exec hermes cardamum card list --json
```

## 添加联系人

用 `card create` 喂入 vCard 内容即可（v0.2.0 接受**原始 vCard 文本**、vCard 文件路径，或 `-` 表示从 stdin 读取），文件名与 `UID` 由 cardamum 自行生成，无需手工 `uuidgen`：

```bash
echo 'BEGIN:VCARD
VERSION:4.0
FN:Name
EMAIL:email@example.com
END:VCARD' | docker compose exec -T hermes cardamum card create -
```

若要绕过 cardamum 直接写文件，注意 vdir 布局：文件放在 `~/.hermes/.contacts/<ADDRESSBOOK-ID>/` 下，文件名用 UUID，vCard 内 `UID` 与文件名一致，且用 `VERSION:4.0`（不是 3.0）。
