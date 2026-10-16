# 静态资源目录

**图片一律不入库**（体积大，`.`gitignore` 已递归排除 `assets/**/*.png|jpg|jpeg|webp|gif`），
只提交本说明。图片由本机直接传到服务器（`scripts/deploy_server.py`），或按下面的来源获取。

## 目录结构（用户整理）

```
assets/items/
├── armor/Level{1..6}/<装备名>.png        护甲（按等级分目录）
├── helmets/Level{1..6}/<装备名>.png      头盔
├── backpacks/Level{1..6}/<装备名>.png    背包
├── chest_rigs/Level{1..5}/<装备名>.png   胸挂（无 6 级）
├── weapon/<装备名>.png                   枪械本体（配件不用图片）
├── backgrounds/<地图名>.png              卡片背景（巴克什/航天基地/潮汐监狱/长弓溪谷/零号大坝/az3 …）
└── html_prototype/原型.png               页面原型（设计参考，同样不入库）
```

## 代码怎么找图（见 `deltaforce_bot/render/assets.py`）

1. 护甲/头盔/背包/胸挂 → 按接口返回的 `grade` 去 `Level{grade}/`，找不到再在同部位其他等级兜底；
2. 枪1/枪2/手枪 → `weapon/`；
3. 配件（部位形如「枪1-瞄具」）→ **不用图片**，卡片上用文字「部位 名称」；
4. 名称归一化：去掉耐久度后缀 `(破损)/(几乎全新)/(全新)`、去掉所有空格、忽略大小写。
   实测 13 款有耐久度变体的装备 13/13 共用同一张贴图，因此去后缀是安全的；
5. 缺图渲染灰色占位块，不影响整张卡片。

## 图片从哪来

- **官方 CDN**：数据源返回的 `pic` 字段就是腾讯官方地址（`playerhub.df.qq.com`），
  `.venv/Scripts/python.exe scripts/fetch_item_images.py` 可批量下载（实测 120 张 3.4 秒 0 失败）；
- 其余由用户自行补齐（武器配件、地图背景等）。

## 背景图处理

卡片渲染时背景会淡化（`opacity: .22` + 深色遮罩），避免压住文字（原型要求）。
