// ball_cands_c.c —— 足球候选（纯 C）：白∧草掩膜 + 连通域筛选
// 输入：BGR 原始帧（W×H×3）
// 输出：每行 "CAND x y w h area"
// 编译：gcc -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard ball_cands_c.c -lm -o ball_cands_c
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned char *mask, *vis;

static void morph3(unsigned char *m, int W, int H, int dilate, unsigned char *tmp) {
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x) {
            int v = dilate ? 0 : 1;
            for (int dy = -1; dy <= 1; ++dy)
                for (int dx = -1; dx <= 1; ++dx) {
                    int ny = y + dy, nx = x + dx;
                    int s2 = (ny < 0 || ny >= H || nx < 0 || nx >= W) ? 0 : m[ny * W + nx];
                    if (dilate) { if (s2) v = 1; }
                    else { if (!s2) v = 0; }
                }
            tmp[y * W + x] = (unsigned char)v;
        }
    memcpy(m, tmp, (size_t)W * H);
}
static int *stack;
static int W, H;

static inline int grass(int b, int g, int r) {
    int mx = b > g ? (b > r ? b : r) : (g > r ? g : r);
    int mn = b < g ? (b < r ? b : r) : (g < r ? g : r);
    int v = mx, s = mx ? (255 * (mx - mn) / mx) : 0;
    int h;
    if (mx == mn) return 0;
    if (mx == r) h = 30 * (g - b) / (mx - mn);
    else if (mx == g) h = 60 + 30 * (b - r) / (mx - mn);
    else h = 120 + 30 * (r - g) / (mx - mn);
    if (h < 0) h += 180;
    return (h >= 30 && h <= 90 && s >= 50 && v >= 35);
}

static inline int white(int b, int g, int r) {
    int mx = b > g ? (b > r ? b : r) : (g > r ? g : r);
    int mn = b < g ? (b < r ? b : r) : (g < r ? g : r);
    int v = mx, s = mx ? (255 * (mx - mn) / mx) : 0;
    return (s < 60 && v > 150);
}

int main(int argc, char **argv) {
    if (argc < 3) { printf("USAGE: ball_cands_c frame.bgr W H\n"); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { printf("INPUT_FAIL\n"); return 1; }
    W = atoi(argv[2]); H = atoi(argv[3]);
    size_t n = (size_t)W * H;
    unsigned char *img = (unsigned char *)malloc(n * 3);
    if (fread(img, 1, n * 3, f) != n * 3) { printf("INPUT_FAIL\n"); return 1; }
    fclose(f);
    mask = (unsigned char *)calloc(n, 1);
    vis = (unsigned char *)calloc(n, 1);
    stack = (int *)malloc(n * sizeof(int));
    if (!mask || !vis || !stack) { printf("OOM\n"); return 1; }

    // 绿场区域 = 绿色掩膜的最大 8 连通域（两遍标记 + 并查集）
    unsigned char *tmpm = (unsigned char *)malloc(n);
    for (int i = 0; i < (int)n; ++i) {
        unsigned char b = img[i * 3], g = img[i * 3 + 1], r = img[i * 3 + 2];
        mask[i] = grass(b, g, r) ? 1 : 0;
    }
    morph3(mask, W, H, 0, tmpm);      // erode
    morph3(mask, W, H, 1, tmpm);      // dilate  -> 开运算
    memcpy(img, img, 0);              // no-op 占位
    int *lab = (int *)calloc(n, sizeof(int));
    int *par = (int *)malloc((size_t)n * sizeof(int));
    if (!lab || !par) { printf("OOM\n"); return 1; }
    for (int i = 0; i < (int)n; ++i) par[i] = i;
    int nextlab = 1;
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x) {
            int i = y * W + x;
            if (!mask[i]) continue;
            int best = 0;
            for (int dy = -1; dy <= 0; ++dy)
                for (int dx = -1; dx <= 1; ++dx) {
                    if (dy == 0 && dx > 0) continue;
                    int ny = y + dy, nx = x + dx;
                    if (ny < 0 || nx < 0 || nx >= W) continue;
                    int l = lab[ny * W + nx];
                    if (l && (best == 0 || l < best)) best = l;
                }
            if (!best) { lab[i] = nextlab++; continue; }
            lab[i] = best;
            for (int dy = -1; dy <= 0; ++dy)
                for (int dx = -1; dx <= 1; ++dx) {
                    if (dy == 0 && dx > 0) continue;
                    int ny = y + dy, nx = x + dx;
                    if (ny < 0 || nx < 0 || nx >= W) continue;
                    int l = lab[ny * W + nx];
                    if (l) { int a = l, bb = best; while (par[a] != a) a = par[a]; while (par[bb] != bb) bb = par[bb]; if (a != bb) par[a] = bb; }
                }
        }
    for (int i = 0; i < (int)n; ++i) {
        if (!lab[i]) continue;
        int a = lab[i];
        while (par[a] != a) a = par[a];
        lab[i] = a;
    }
    int *cnt = (int *)calloc((size_t)nextlab, sizeof(int));
    for (int i = 0; i < (int)n; ++i) if (lab[i]) cnt[lab[i]]++;
    int bestlab = 0, bestcnt = 0;
    for (int l = 1; l < nextlab; ++l) if (cnt[l] > bestcnt) { bestcnt = cnt[l]; bestlab = l; }
    for (int i = 0; i < (int)n; ++i) {
        unsigned char b = img[i * 3], g = img[i * 3 + 1], r = img[i * 3 + 2];
        mask[i] = (lab[i] == bestlab && white(b, g, r)) ? 1 : 0;
    }
    free(cnt); free(lab); free(par);

    const int min_area = 12, max_area = 1500;
    for (int y0 = 0; y0 < H; ++y0) {
        for (int x0 = 0; x0 < W; ++x0) {
            int idx = y0 * W + x0;
            if (!mask[idx] || vis[idx]) continue;
            int sp = 0;
            stack[sp++] = idx;
            vis[idx] = 1;
            int area = 0, xmin = x0, xmax = x0, ymin = y0, ymax = y0, per = 0;
            while (sp > 0) {
                int p = stack[--sp];
                int py = p / W, px = p - py * W;
                ++area;
                {
                    int edge = 0;
                    for (int dy = -1; dy <= 1; ++dy)
                        for (int dx = -1; dx <= 1; ++dx) {
                            int ny = py + dy, nx = px + dx;
                            if (ny < 0 || ny >= H || nx < 0 || nx >= W || !mask[ny * W + nx]) edge = 1;
                        }
                    per += edge;
                }
                if (px < xmin) xmin = px;
                if (px > xmax) xmax = px;
                if (py < ymin) ymin = py;
                if (py > ymax) ymax = py;
                for (int dy = -1; dy <= 1; ++dy)
                    for (int dx = -1; dx <= 1; ++dx) {
                        int ny = py + dy, nx = px + dx;
                        if (ny < 0 || ny >= H || nx < 0 || nx >= W) continue;
                        int q = ny * W + nx;
                        if (mask[q] && !vis[q]) { vis[q] = 1; stack[sp++] = q; }
                    }
            }
            if (area < min_area || area > max_area) continue;
            int bw = xmax - xmin + 1, bh = ymax - ymin + 1;
            if (bh == 0) continue;
            float ar = (float)bw / bh;
            if (ar < 0.4f || ar > 2.5f) continue;
            float circ = per > 0 ? (float)(4.0 * 3.14159265 * area) / (float)(per * per) : 0.f;
            if (circ < 0.45f) continue;
            float cx = (xmin + xmax) * 0.5f, cy = (ymin + ymax) * 0.5f;
            float cw = 0.25f * W, ch = 0.22f * H;
            int corner = (cx < cw && cy < ch) || (cx > W - cw && cy < ch) ||
                         (cx < cw && cy > H - ch) || (cx > W - cw && cy > H - ch);
            if (corner) continue;
            printf("CAND %.1f %.1f %d %d %d\n", cx, cy, bw, bh, area);
        }
    }
    return 0;
}