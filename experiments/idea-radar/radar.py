"""Independent Feishu entry for evidence-backed product discovery."""
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "project-chemist"))
from mobile_core import Store
from mobile import main


class RadarStore(Store):
    help_text = (
        "我是开发方向雷达。\n"
        "发送“找方向＋条件”，例如：找方向，适合个人开发者、两周能验证的 SaaS。\n"
        "发送“找小游戏＋条件”，或发送产品/项目网页链接，分析已有方向。\n"
        "之后可以继续追问；新的找方向指令或链接会开启新话题。\n"
        "报告包含候选、讨论/增长/付费线索、国内适配假设、缺口和最小验证步骤。\n"
        "“状态”查看进度，“结果”重发报告，“新话题”重置上下文。\n"
        "本机运行，按需查询公开网页；搜索未覆盖的内容会明确说明。")
    new_topic_text = "已开始新话题。请描述想找的开发方向、时间和技能条件，或发送一个项目链接。历史报告保留。"
    retry_text = "可重新发送原问题或项目链接重试。"

    def request_target(self, text, previous):
        # Validate public URL syntax here and again before any reader call.
        from radar_analysis import extract_url
        url = extract_url(text)
        if url:
            return url
        if not previous or re.match(r"^(?:找方向|找小游戏|寻找方向|重新找|发现项目)", text):
            return "radar:discovery"
        return None


if __name__ == "__main__":
    try:
        raise SystemExit(main(store_type=RadarStore, analysis_script=HERE / "radar_analysis.py",
                              default_state=REPO / "work/idea-radar"))
    except Exception as error:
        print("开发方向雷达启动失败：" + type(error).__name__ + "；请检查本机日志。", file=sys.stderr)
        raise SystemExit(1)
