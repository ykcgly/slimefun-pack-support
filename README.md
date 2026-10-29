# 粘液科技材质包

一个支持高版本slimefun的**纯客户端**材质包！  
前提是服务端没有动其他东西...

<img width="687" height="501" alt="菜单界面" src="https://github.com/user-attachments/assets/017e9cc8-b921-44bc-8983-49cbd908167d" />

## 涵盖附属

1. slimefun4-粘液科技
<img width="687" height="492" alt="粘液科技" src="https://github.com/user-attachments/assets/8877f965-0550-48c5-8a3e-882d66a179f5" />
1. Bump-魔法
<img width="434" height="75" alt="魔法" src="https://github.com/user-attachments/assets/97f7987b-d121-4df1-9acc-cdb4a4a6e259" />
1. Cultivation-农耕工艺

2. DyedBackpacks-染色背包
3. ElectricSpawners-电力刷怪笼
4. ExoticGarden-异域花园
5. ExtraGear-更多装备
<img width="648" height="285" alt="更多装备" src="https://github.com/user-attachments/assets/c1422cb2-eb40-4082-891f-ce65f502f255" />
6. FluffyMachines-蓬松科技
<img width="513" height="81" alt="蓬松科技" src="https://github.com/user-attachments/assets/3d990218-7f4e-4c97-ad5c-0407984d2aa9" />
1.  FoxyMachines-神秘科技
2.  Gastronomicon-美食家
3.  InfinityExpansion-无尽贪婪
<img width="648" height="435" alt="无尽贪婪" src="https://github.com/user-attachments/assets/68873ec9-5479-498f-b0cf-41c156d9bdb0" />
1.  InfinityExpansion2-无尽贪婪2
2.  Liquid-流体
3.  SimpleUtils-简易工具
4.  Slimefunwarfare-战争工艺
<img width="646" height="141" alt="战争工艺" src="https://github.com/user-attachments/assets/239d97c8-55c2-4f55-a342-fb76c37f1dfd" />
1.  Slimetinker-粘液匠魂
2.  SlimyTreeTaps-粘液木龙头
3.  Supreme-至尊

## 构建工作流（维护者）

本包不再手工维护深层嵌套的 `items/*.json`，而是 平铺清单 + 自动构建：

```
data\itemdefs\<base>.json        # 人工维护的 Source of Truth（平铺条目）
data\itemdefs-extra\<base>.json  # 附属附加条目（自动并入上方清单）
tools\build_items.ps1 -Stage all # migrate → add → build（含等价校验）
tools\build_items.ps1 -Stage verify  # 随时校验清单与产物一致
```

### 产物结构的选择（重要）

构建器按 Gson 嵌套上限自动选结构（Minecraft 的 `JsonReader` 对物品定义文件有 **255 层硬嵌套限制**，超限整个文件解析失败 → 该 base 下**所有物品紫黑**）：

| 条目深度 | 结构 | 说明 |
|---|---|---|
| 条目数 + 3 + tail 深度 ≤ 250 | 线性 condition 链 | 与历史实测可用的 314 文件字节一致 |
| 超限（目前仅 `player_head`，321 条） | **扁平 select** | 深度恒定 ~5，与条目数量无关 |

扁平 select 产物形态：

```json
{ "model": {
    "type": "minecraft:select",
    "property": "minecraft:component",
    "component": "minecraft:custom_data",
    "cases": [
      { "when": [
          "{PublicBukkitValues:{\"slimefun:slimefun_item\":\"<ID>\"}}",
          "{PublicBukkitValues:'{\"slimefun:slimefun_item\":\"<ID>\"}'}"
        ],
        "model": { "type": "model", "model": "slimefun:..." } }
    ],
    "fallback": { ...tail（原版头颅渲染，原样保留）... }
} }
```

语义说明：

- **`when` 走的是 custom_data 组件自身的解码路径**：字符串形式会被解析为 SNBT 后得到 CompoundTag，再与物品 actual custom_data 做**精确的 Tag 相等比较**（键序无关、结构需一致，非子集匹配）。
- **`when` 中任一字符串解析失败都会导致整个物品定义文件解析失败（该 base 全部紫黑）**——这是实测确认过的失败模式（2026-10：`player_head` 出现 321 条 `Couldn't parse item model`、所有头颅紫黑）。当前产物只写入可正常解析的两种形态：
  1. 嵌套复合：`{PublicBukkitValues:{"slimefun:slimefun_item":"<ID>"}}`
  2. Bukkit PDC 字符串嵌套：`{PublicBukkitValues:'{"slimefun:slimefun_item":"<ID>"}'}`
  - 第 2 种必须用 **SNBT 单引号**包裹内层 JSON：内层本身含 `"`，写成 `{PublicBukkitValues:"{\"...\"}"}` 需要两层转义，极易漏掉反斜杠而变成非法 SNBT。
  - 裸键不允许含 `:`，即 `{PublicBukkitValues:{slimefun:slimefun_item:"<ID>"}}` 是**非法**的。
  - 构建期对每个 `when` 都会跑 `McJson.Snbt` 校验（`tools\json.cs`），非法字符串会直接中止构建，不会再写进材质包。
- **`when` 全部 miss 时走 `fallback`**：渲染原版头颅——优雅降级，不会紫黑。
- **附属条目的 ID 必须按插件源码推导，不能凭直觉拼**（2026-10 踩坑：染色背包全部写成 `DYED_BACKPACK_SMALL_*`，96 个 case 一条都不命中）：
  - 染色背包：`"DYED_" + backpack.getItemId() + "_" + color.name()` → `DYED_SMALL_BACKPACK_LIME`（`SMALL/MEDIUM/LARGE/WOVEN/GILDED/RADIANT` + `_BACKPACK`，共 6×16=96）
  - 电力刷怪笼：`"ELECTRIC_SPAWNER_" + mob` → `ELECTRIC_SPAWNER_BLAZE`（生物表见插件 `config.yml`，共 27）
  - `StageAdd` 现在会断言染色背包的基准 ID 必须已存在于 `data\itemdefs\player_head.json`，拼错直接中止构建。
- 若某些条目材质未命中（显示为原版头颅），通常是该物品 custom_data 里还有其他键；此时可以让用户在服务端 `/data get` 抽样该物品后，把实际 custom_data 结构补充到 `tools\build_items.ps1` 的 `SelectWhenVars` 再重跑 `-Stage build`。
- 构建有双重等价校验：换入前先验证当前文件能被新清单完全解释，换入后再验证暂存产物与清单一致，任何不一致都会中止并保留原文件。

## 说明

本资源来源于网络！  
如有侵权可以联系我。
邮箱：`1424136122@qq.com`
QQ：`1424136122`