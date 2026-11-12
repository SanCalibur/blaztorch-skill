# 炬火 AI 赛事与学习顾问

让支持 Agent Skills 的 AI 从[炬火官网](https://blaztorch.com/resources)查找黑客松、AI课程和获奖作品，并结合你的具体经历、时间与参赛约束推荐比赛。

## 安装

使用 [Skills CLI](https://github.com/vercel-labs/skills)（需Node.js 22.20+）：

```sh
npx skills add SanCalibur/blaztorch-skill --skill blaztorch-guide
```

按提示选择AI工具。安装到当前项目是CLI默认行为；需要全局安装时加`-g`。

也可以克隆仓库，把`skills/blaztorch-guide`整个文件夹复制到所用AI工具的技能目录。技能入口是[SKILL.md](skills/blaztorch-guide/SKILL.md)，运行读取脚本需要Python 3.10+，无需额外Python包或网站账号。没有Python时，技能会使用AI已有的浏览工具读取官网。

## 你可以这样问

```text
使用 $blaztorch-guide，找5场适合个人线上参加、还有时间准备的AI黑客松。
我跟教程做过网页、调用过模型API，每周有8小时。先评估我的准备度，再推荐比赛。
我正在准备RAG应用比赛，帮我选公开课程和可研究的获奖项目。
我不想做纯聊天机器人，从获奖作品里推荐几个产品思路，并说明为什么适合我研究。
```

## 它会做什么

- **查询赛事**：说明日程、形式、公开资格与来源，区分报名截止、开发/提交阶段和结果公布。
- **推荐学习资料**：说明先修、公开材料、登录与费用边界，按当前缺口安排学习顺序。
- **分析获奖作品**：保留具体奖项和赛道范围，提供产品思路、原始页面与代码/演示入口。
- **评估能力**：从具体经历判断编程、AI接入、数据、交付、产品、表达六个维度，未知保留未知。
- **匹配比赛**：先看是否能参加，再看能力、时间和队伍；给出适合尝试、补强后考虑或暂不建议的原因。

默认精选少量结果，附官网档案与原始来源。AI会进一步核对官方规则，尤其是地区/身份、组队、费用、AI工具限制与已有项目政策。候补、审核录取、尚未开放和资料待确认会分别说明。

## 读取方式与边界

**脚本搬运数据，agent承担全部判断。** 固定脚本只做公开页面请求、解析、缓存、字段整理、分页和显式条件查询；能力评估、时间状态判断、资格核验、难度判断、作品去重、匹配及推荐排序均由使用技能的agent完成。脚本不接收个人能力档案，没有`recommend`或`profile-template`命令。

当前版本从官网公开页面向访客发送的展示数据读取，不依赖受保护的CMS接口，不附带过时的全量数据快照。网页格式变化时明确报错并切换浏览器读取；不会把解析失败当成没有结果。

公共数据缓存15分钟，最多40份页面；搜索通常返回5项，获奖作品最多查看3页。能力资料不自动持久保存，也不会发送到炬火或报名平台。脚本只向炬火公开资源页面发出GET请求，不访问管理员接口，不提交报名、不付款、不改官网。

能力匹配是有依据的准备度建议，不是获奖概率。只有日期或时区不完整时保留不确定区间；读取时间不等于原始资料核对时间。使用`--refresh`可重新读取官网，离线模式会明确标注缓存。

## 直接运行

```sh
python skills/blaztorch-guide/scripts/blaztorch.py search --kind events --query 智能体 --limit 5
python skills/blaztorch-guide/scripts/blaztorch.py search --kind learning --query RAG --limit 5
python skills/blaztorch-guide/scripts/blaztorch.py search --kind winners --query Noddle --limit 1
python skills/blaztorch-guide/scripts/blaztorch.py search --kind events --offset 5 --limit 5
python skills/blaztorch-guide/scripts/blaztorch.py --refresh detail --kind events --id <公开编号>
```

`--status`、`--level`、`--access`只精确匹配官网现有字段，不解释它们；不指定时保留所有值。关键词采用字面匹配，不自动联想同义词。搜索保持来源顺序，agent可查看更多页面后自行选出推荐。官网给出的状态、日程和奖项都保留原始字段与来源，交由agent核对。

字段与浏览器备用路线见[官网读取说明](skills/blaztorch-guide/references/site-access.md)，评估方法见[能力与匹配说明](skills/blaztorch-guide/references/assessment.md)。

## 开发与验证

```sh
python -m unittest discover -s tests -v
python -m compileall -q skills/blaztorch-guide/scripts
```

离线测试覆盖页面格式、长中文文本、公开字段边界、缓存、分页，以及日期/状态原样保留、不预筛已结束赛事、不按URL合并作品等数据搬运边界。agent判断场景见[行为验收案例](tests/agent-scenarios.md)，需在使用技能的agent会话中检验，不能用脚本打分代替。真实官网连接可使用上述只读命令验证。

## 许可

本仓库技能与辅助代码采用[MIT许可](LICENSE)。官网和第三方作品、课程的内容与代码仍遵循其各自的条款和许可；本仓库许可不替它们授权。
