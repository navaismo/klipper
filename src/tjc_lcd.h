#ifndef __TJC_LCD_H
#define __TJC_LCD_H
#include <stdint.h>

#define TJC_FRAME_DATA_MAX 48
uint_fast8_t tjc_lcd_send_frame(uint8_t opcode, const uint8_t *data,
                               uint8_t len);
uint_fast8_t tjc_lcd_job_claim(uint8_t oid);
void tjc_lcd_job_release(uint8_t error);
uint_fast8_t tjc_lcd_fence_start(void);
uint_fast8_t tjc_lcd_fence_ready(void);

#endif
