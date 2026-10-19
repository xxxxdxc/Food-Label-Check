import type { FieldStatus, IssueStatus, TaskStatus } from "../api/types";

export type Tone = { label: string; bg: string; fg: string; border?: string };

export const ISSUE_STATUS: Record<IssueStatus, Tone> = {
  FAIL: { label: "问题", bg: "#fbe8e5", fg: "#9e3d35", border: "#efc9c3" },
  REVIEW: { label: "待复核", bg: "#fff0db", fg: "#80531a", border: "#e9d9ad" },
  NOT_CHECKED: { label: "未检查", bg: "#eff1ee", fg: "#4f5b54", border: "#dfe4de" },
  PASS: { label: "通过", bg: "#e5f4e6", fg: "#2c6c48", border: "#c9e5cc" },
};

export const FIELD_STATUS: Record<FieldStatus, Tone> = {
  ready: { label: "已就绪", bg: "#e7f5e8", fg: "#2c6c48" },
  needs_review: { label: "待复核", bg: "#fff0db", fg: "#80531a" },
  missing: { label: "缺失", bg: "#eff1ee", fg: "#4f5b54" },
  human_confirmed: { label: "已人工确认", bg: "#e4eff5", fg: "#245f78" },
};

export const TASK_STATUS: Record<TaskStatus, Tone> = {
  QUEUED: { label: "等待审核", bg: "#eff1ee", fg: "#4f5b54" },
  EXTRACTING: { label: "分区与识别中", bg: "#e4eff5", fg: "#245f78" },
  WAITING_REVIEW: { label: "等待人工复核", bg: "#fff0db", fg: "#80531a" },
  CHECKING: { label: "检查中", bg: "#e4eff5", fg: "#245f78" },
  COMPLETED: { label: "预审完成", bg: "#e5f4e6", fg: "#2c6c48" },
  PARTIAL: { label: "部分检查完成", bg: "#eff1ee", fg: "#3f4b44" },
  FAILED: { label: "失败", bg: "#fbe8e5", fg: "#9e3d35" },
};

export const LAYER: Record<string, Tone> = {
  L1: { label: "标准化审核", bg: "#e5f4df", fg: "#2a6345" },
  L2: { label: "渠道要求审核", bg: "#e4eff5", fg: "#245f78" },
  L3: { label: "人工判断", bg: "#faf0dc", fg: "#7c541c" },
};

// 任务进入这些状态后停止轮询。
export const SETTLED: TaskStatus[] = ["COMPLETED", "PARTIAL", "FAILED", "WAITING_REVIEW"];

export const NUTRIENTS: { key: string; label: string; unit: string }[] = [
  { key: "energy", label: "能量", unit: "kJ" },
  { key: "protein", label: "蛋白质", unit: "g" },
  { key: "fat", label: "脂肪", unit: "g" },
  { key: "saturated_fat", label: "饱和脂肪", unit: "g" },
  { key: "trans_fat", label: "反式脂肪", unit: "g" },
  { key: "carbohydrate", label: "碳水化合物", unit: "g" },
  { key: "sugar", label: "糖", unit: "g" },
  { key: "dietary_fiber", label: "膳食纤维", unit: "g" },
  { key: "sodium", label: "钠", unit: "mg" },
  { key: "cholesterol", label: "胆固醇", unit: "mg" },
  { key: "calcium", label: "钙", unit: "mg" },
];
const NUTRIENT_NAME = Object.fromEntries(NUTRIENTS.map((n) => [n.key.toUpperCase(), n.label]));

const FIELD_NAME: Record<string, string> = {
  FOOD_NAME: "食品名称", NET_CONTENT: "净含量", INGREDIENTS_TEXT: "配料表", PRODUCER: "生产者信息",
  SHELF_LIFE: "保质期", STORAGE: "贮存条件", LICENSE_NUMBER: "生产许可证编号",
};
const CLAIM_NAME: Record<string, string> = {
  HIGH_PROTEIN: "高蛋白声称", HIGH_FIBER: "高纤维声称", LOW_SUGAR: "低糖声称", SUGAR_FREE: "无糖声称",
};
const EXACT: Record<string, string> = {
  "COURSE-100G-001": "企业渠道要求：营养表采用每100g口径",
  "OCR-DOWNSTREAM-GATE": "营养表未达到可计算条件",
  "OCR-DUPLICATE-METRIC": "营养项重复",
  "OCR-FIELD-GATE": "营养项待复核",
  "OCR-NRV-GATE": "NRV% 待复核",
  "OCR-CLAIM-GATE": "声称文字待复核",
  "OCR-LOW-CONFIDENCE": "识别可信度偏低",
  "OCR-ERROR": "识别错误",
  "INGREDIENT-ORDER": "配料顺序",
  "INGREDIENT-INPUT": "配料信息不完整",
  "INGREDIENT-AMOUNT": "配料占比",
  "CROSS-NET_CONTENT": "跨位置净含量一致性",
  "ENERGY-INPUT-GATE": "能量算式输入未确认",
  "ENERGY-ARITHMETIC": "能量换算核对",
  "ENERGY-MISSING": "缺少能量",
  "INPUT-BASIS": "营养表基准",
  "INPUT-FIELD": "输入字段",
  "DUPLICATE-METRIC": "营养项重复",
  "NRV-BLANK": "NRV% 未标示",
  "NRV-NOT-DEFINED": "该营养项无 NRV",
  "NRV-UNSUPPORTED": "NRV 暂不支持",
  "NRV-INPUT": "NRV 输入",
  "CLAIM-UNKNOWN": "暂未被固定规则覆盖的声称",
  "ACTUAL-MISSING": "缺少实测值",
  "ACTUAL-UNSUPPORTED": "实测值暂不支持",
  "ACTUAL-INPUT": "实测值输入",
  "CROSS-METRIC": "跨位置营养项一致性",
  "CROSS-INPUT": "跨位置输入",
  "CROSS-UNIT": "跨位置单位一致性",
  "HUMAN-QUESTION": "需人工判断的问题",
  "PACKAGE-INPUT": "包装信息输入",
  "PACKAGE-FIELDS": "必填字段完整性",
  "ALLERGEN-INPUT": "过敏原信息输入",
  "CHANNEL-NO-KB": "渠道知识库未配置",
  "CHANNEL-NO-CONFIRMED-RULES": "渠道暂无已确认规则",
  "CHANNEL-CONFLICT": "渠道要求冲突",
};

const named = (map: Record<string, string>, key: string) => map[key] ?? key;

// 规则 ID 由后端动态拼接：先精确匹配，再按前缀映射；映射不到返回 null，界面改显示规则 ID。
export function ruleTitle(id: string): string | null {
  if (EXACT[id]) return EXACT[id];
  const after = (prefix: string) => id.slice(prefix.length);
  if (id.startsWith("NRV-100G-")) return named(NUTRIENT_NAME, after("NRV-100G-")) + " NRV% 核对";
  if (id.startsWith("NUTRITION-MISSING-")) return "缺少" + named(NUTRIENT_NAME, after("NUTRITION-MISSING-"));
  if (id.startsWith("ACTUAL-ZERO-")) return named(NUTRIENT_NAME, after("ACTUAL-ZERO-")) + " 零值界限";
  if (id.startsWith("ACTUAL-")) return named(NUTRIENT_NAME, after("ACTUAL-")) + " 允许误差";
  if (id.startsWith("PACKAGE-FIELD-")) return named(FIELD_NAME, after("PACKAGE-FIELD-")) + "（有无）";
  if (id.startsWith("CLAIM-")) return CLAIM_NAME[after("CLAIM-")] ?? "声称：" + after("CLAIM-");
  if (id.startsWith("ALLERGEN-")) return "过敏原提示：" + ({GLUTEN: "麸质", EGG: "蛋类", MILK: "乳类", SOY: "大豆", PEANUT: "花生"}[after("ALLERGEN-")] ?? after("ALLERGEN-"));
  if (id.startsWith("CHANNEL-CANDIDATE-")) return "渠道候选要求";
  if (id.startsWith("CHANNEL-")) return "渠道要求";
  if (id.startsWith("CROSS-")) return "跨位置一致性";
  return null;
}

export const TABLE_CODE: Record<string, string> = {
  TABLE_STRUCTURE_UNCERTAIN: "营养表行列结构存在异常",
  TABLE_NOT_DETECTED: "未检测到营养成分表",
  TABLE_V2_NOT_FOUND: "表格识别失败，仅有全文候选行",
  TABLE_COUNT_MISMATCH: "表格数量不一致",
  FULLTEXT_NUTRIENT_OMITTED: "结构化表格漏掉了营养项",
  TABLE_LAYOUT_RECOVERED: "版面已自动恢复，需核对",
  VISION_CANDIDATE_UNVERIFIED: "识别结果待逐行确认",
  NON_NUTRITION_TABLE: "图中可能不是营养成分表",
};

export const SOURCE_TYPE: Record<string, string> = {
  baidu_accurate: "文字 OCR",
  baidu_table_v2: "表格 OCR",
  codex_vision: "图像识别记录",
  manual: "人工",
};

// 复核队列里的 path 形如 facts.nutritionTables[0].rows[1].amount，转成可读名称。
export function pathLabel(path: string): string {
  const basic: Record<string, string> = {
    "facts.productName": "商品名称",
    "facts.netContent": "净含量",
    "facts.ingredientsText": "配料",
    "facts.allergenText": "过敏原",
  };
  if (basic[path]) return basic[path];
  const row = path.match(/rows\[(\d+)\]\.(\w+)$/);
  if (row) {
    const part = { nutrient: "营养项名称", amount: "含量", nrvPercent: "NRV%" }[row[2]] ?? row[2];
    return `第 ${Number(row[1]) + 1} 行 · ${part}`;
  }
  if (path.endsWith(".basis.servingSize")) return "每份份量";
  if (path.endsWith(".basis")) return "表头基准";
  const claim = path.match(/claims\[(\d+)\]/);
  if (claim) return `声称 ${Number(claim[1]) + 1}`;
  if (path.includes("barcodes")) return "条码";
  return path;
}

export const SCOPE_TEXT =
  "GB 28050-2025 新版预检（2027-03-16 起实施）；GB 7718 仅覆盖必填字段有无与可计算的配料顺序。结果仅针对已确认的数据，覆盖范围有限，不等同人工签核。";

const REVIEW_REASON: Record<string, string> = {
  OCR_LOW_CONFIDENCE: "OCR 可信度低于96%", LOW_OCR_CONFIDENCE: "OCR 可信度低于96%",
  OCR_CONFIDENCE_MISSING: "OCR 未返回可信度", OCR_TEXT_ANOMALY: "文本格式异常，请检查括号或符号",
  OCR_REGION_EMPTY: "已找到文字区域，但 OCR 未得到有效文字",
  REGION_FIELD_CONFLICT: "不同区域识别出不同值", MULTI_IMAGE_CONFLICT: "不同图片识别出不同值",
  OCR_ENGINE_DISAGREEMENT: "文字 OCR 与表格单元格无法对应",
  TABLE_V2_UNCORROBORATED: "表格字段缺少可信度可核验的文字识别",
  TABLE_STRUCTURE_UNCERTAIN: "表格行列关系不确定", OCR_TABLE_HEADER_UNCERTAIN: "表头基准需核对",
  MULTIPLE_NUTRIENTS_IN_CELL: "一个单元格包含多个营养项", UNKNOWN_NUTRIENT: "营养项名称无法解析",
  NUMBER_OR_UNIT_UNREADABLE: "含量或单位无法解析", UNIT_MISSING: "未识别到单位",
  NUTRIENT_UNIT_MISMATCH: "单位与该营养项不匹配", AMOUNT_EXCEEDS_100G_BASIS: "每100克含量超过100克，请检查小数点",
  DUPLICATE_NUTRIENT: "营养项重复，请核对行列对应关系",
  MULTIPLE_QUANTITIES: "同一区域出现多个数量", NRV_UNREADABLE: "NRV% 无法解析",
  LOCATION_UNKNOWN: "文字无法定位到原图", LAYOUT_FALLBACK_UNVERIFIED: "表格行列由位置恢复，需核对",
  VISION_DISAGREEMENT: "识别记录存在差异，请对照原图核对", VISION_CANDIDATE_UNVERIFIED: "识别结果尚未确认，请对照原图核对",
  CELL_SPAN_UNSUPPORTED: "表格含跨行或跨列单元格，请核对对应关系",
  V2_MISSING_NUTRIENT_ROWS: "表格识别缺少营养项行", ZERO_NUTRITION_ROWS: "未识别到有效营养项行",
  NUTRIENT_ROW_AMBIGUOUS: "营养项与所在行无法明确对应",
  AMOUNT_MISSING_OR_UNREADABLE: "含量缺失或无法解析", MERGED_NUTRIENT_AMOUNT_CELL: "营养项与含量合并在同一单元格",
  NRV_RECOVERED_FROM_ACCURATE: "NRV% 由文字 OCR 补充，请核对所在行", NRV_ROW_AMBIGUOUS: "NRV% 与所在行无法明确对应",
  BARCODE_CHECKSUM_INVALID: "条码位数或校验位无效", BASIS_UNKNOWN: "无法确定营养表计量基准",
};
export const reviewReason = (code: string) => REVIEW_REASON[code] ?? code;
