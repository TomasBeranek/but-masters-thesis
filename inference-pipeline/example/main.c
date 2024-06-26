// *****************************************************************************
//  File:            main.c
//  Master's Thesis: Evaluating Reliability of Static Analysis Results
//                   Using Machine Learning
//  Author:          Beranek Tomas (xberan46)
//  Date:            14.5.2024
//  Up2date sources: https://github.com/TomasBeranek/but-masters-thesis
//  Description:     Project for demonstrating the inference pipeline.
// *****************************************************************************

#include <stdlib.h>
#include <stdio.h>
#include "add_one.h"

int f(int a) {
  return add_one(a);
}

int g(int b) {
  return b;
}

/* An unnecessarily complicated program which returns 0, if no args were passed
   otherwise it returns the number of args passed (program name included). But
   when some args are passed NULL_DEREFERENCE happens in add_one function. */
int main(int argc, char const *argv[]) {
  int x = argc - 1;
  int y;

  printf("%d\n", x);

  if (x > 0) {
    y = f(x);
  } else {
    y = g(x);
  }

  int *f = NULL;
  *f = 10;

  return y;
}
