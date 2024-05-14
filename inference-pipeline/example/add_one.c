// *****************************************************************************
//  File: 					 add_one.c
//  Master's Thesis: Evaluating Reliability of Static Analysis Results
//                   Using Machine Learning
//  Author: 				 Beranek Tomas (xberan46)
//  Date: 					 14.5.2024
//  Up2date sources: https://github.com/TomasBeranek/but-masters-thesis
//  Description:		 Project for demonstrating the inference pipeline.
// *****************************************************************************

#include "add_one.h"
#include <stdlib.h>

int add_one(int x){
  if (x) {
    int * p = NULL;
    x = *p;
  } else {
    return x + 1;
  }
}
